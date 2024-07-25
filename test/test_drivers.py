"""Driver unit tests."""

import abc
import re
from typing import Any
from typing import ClassVar
from unittest import mock

import faker
from kubernetes.client import models
import pytest

from kwait import drivers
from kwait import inventory

# pylint: disable=missing-docstring,redefined-outer-name


class DriverTest(abc.ABC):
    """DriverTest is a helper for running tests on drivers.

    Instantiating this class gets you a callable object that can be
    used to run individual test cases against the driver class passed
    in to the constructor. It handles:

    * Creating the `ResourceDescriptor` object to test on;
    * Mocking the necessary Kubernetes client APIs;
    * Asserting the appropriate result.
    """

    driver_cls: ClassVar
    fake = faker.Faker()
    function_name: str | None = None

    _camel_case_re = re.compile(r"(?<!^)(?=[A-Z])")

    def basic_test(
        self,
        mock_obj: Any,  # the k8s model objects have no common superclass
        state: str | None = None,
        state_contains: str | None = None,
    ) -> None:
        """Run a single test case.

        If `state` is set, then `is_ready()` must return a false
        value, and the `state` field of the result must exactly match.

        If `state_contains` is set, then `is_ready()` must return a
        false value, and the `state` of the result must contain the
        substring given.

        If neither `state` nor `state_contains` is set, then
        `is_ready()` must return a true value.
        """
        if self.function_name is not None:
            function_name = self.function_name
        else:
            camel_case_kind = self._camel_case_re.sub("_", self.driver_cls.kind).lower()
            function_name = f"read_namespaced_{camel_case_kind}"

        resource = inventory.ResourceDescriptor(
            self.driver_cls.api_version,
            self.driver_cls.kind,
            self.fake.word(),
            self.fake.word(),
        )

        if self.driver_cls.api_version == "apps/v1":
            k8s_api_object_name = "AppsV1Api"
        elif self.driver_cls.api_version == "v1":
            k8s_api_object_name = "CoreV1Api"
        elif self.driver_cls.api_version == "policy/v1":
            k8s_api_object_name = "PolicyV1Api"
        else:
            pytest.fail(f"Unsupported API version: {self.driver_cls.api_version}")

        mock_api_client = mock.Mock()
        with mock.patch(f"kubernetes.client.{k8s_api_object_name}") as mock_api:
            getattr(mock_api.return_value, function_name).return_value = mock_obj

            driver = self.driver_cls(mock_api_client, resource)

            result = driver.is_ready()
            expected = state is None and state_contains is None
            assert bool(result) == expected
            assert result.is_ready == expected
            assert result.resource == resource
            if state:
                assert result.state == state
            if state_contains:
                assert state_contains in result.state

        mock_api.assert_called_once_with(mock_api_client)

    @abc.abstractmethod
    def get_object(self) -> Any:
        """Get an object to be tested by this class.

        The object returned by `get_object()` must be in a "ready"
        state; individual test cases can manipulate it to be unready
        or in different ready states.
        """

    def test_ready(self) -> None:
        self.basic_test(self.get_object())


class GenerationDriverTest(DriverTest):
    def test_wrong_observed_generation(self) -> None:
        obj = self.get_object()
        obj.status.observed_generation = self.fake.unique.pyint()
        self.basic_test(obj, state="wrong generation")

    def test_wrong_metadata_generation(self) -> None:
        obj = self.get_object()
        obj.metadata.generation = self.fake.unique.pyint()
        self.basic_test(obj, state="wrong generation")


class ReplicasDriverTest(GenerationDriverTest):
    def test_wrong_status_replicas(self) -> None:
        obj = self.get_object()
        obj.status.replicas = self.fake.unique.pyint()
        self.basic_test(obj, state_contains="replicas exist")

    def test_wrong_spec_replicas(self) -> None:
        obj = self.get_object()
        obj.spec.replicas = self.fake.unique.pyint()
        self.basic_test(obj, state_contains="replicas exist")

    def test_wrong_ready_replicas(self) -> None:
        obj = self.get_object()
        obj.status.ready_replicas = self.fake.unique.pyint()
        self.basic_test(obj, state_contains="replicas ready")

    def test_wrong_available_replicas(self) -> None:
        obj = self.get_object()
        obj.status.available_replicas = self.fake.unique.pyint()
        self.basic_test(obj, state_contains="replicas available")


class TestDaemonSet(GenerationDriverTest):
    driver_cls = drivers.DaemonSet

    def get_object(self) -> models.V1DaemonSet:
        generation = self.fake.unique.pyint()
        ready = self.fake.unique.pyint()
        return models.V1DaemonSet(
            metadata=models.V1ObjectMeta(generation=generation),
            status=models.V1DaemonSetStatus(
                observed_generation=generation,
                number_ready=ready,
                number_available=ready,
                desired_number_scheduled=ready,
                current_number_scheduled=ready,
                number_misscheduled=0,
            ),
        )

    daemonset = pytest.fixture(get_object)

    def test_not_available(self, daemonset: models.V1DaemonSet) -> None:
        daemonset.status.number_available = self.fake.unique.pyint()
        self.basic_test(daemonset, state_contains="pods available")

    def test_not_ready(self, daemonset: models.V1DaemonSet) -> None:
        daemonset.status.number_ready = self.fake.unique.pyint()
        self.basic_test(daemonset, state_contains="pods ready")


class TestDeployment(ReplicasDriverTest):
    driver_cls = drivers.Deployment

    def get_object(self) -> models.V1Deployment:
        generation = self.fake.unique.pyint()
        replicas = self.fake.unique.pyint()
        return models.V1Deployment(
            metadata=models.V1ObjectMeta(generation=generation),
            status=models.V1DeploymentStatus(
                observed_generation=generation,
                replicas=replicas,
                ready_replicas=replicas,
                available_replicas=replicas,
                conditions=[
                    models.V1DeploymentCondition(
                        type="Progressing",
                        status="True",
                        reason="NewReplicaSetAvailable",
                    ),
                    models.V1DeploymentCondition(
                        type="Available",
                        status="True",
                    ),
                ],
            ),
            spec=models.V1DeploymentSpec(
                replicas=replicas,
                selector=models.V1LabelSelector(),
                template=models.V1PodTemplateSpec(),
            ),
        )

    deployment = pytest.fixture(get_object)

    def test_unavailable_condition(self, deployment: models.V1Deployment) -> None:
        condition_type = self.fake.word()
        deployment.status.conditions.append(
            models.V1DeploymentCondition(
                type=condition_type,
                status="False",
            )
        )
        self.basic_test(deployment, state=condition_type.lower())

    def test_failure_condition(self, deployment: models.V1Deployment) -> None:
        deployment.status.conditions.append(
            models.V1DeploymentCondition(
                type="ReplicaFailure",
                status="True",
            )
        )
        self.basic_test(deployment, state="replica failure")

    def test_no_conditions(self, deployment: models.V1Deployment) -> None:
        deployment.status.conditions = []
        self.basic_test(deployment, state="no conditions")

    def test_status_replicas_none(self) -> None:
        obj = self.get_object()
        obj.status.replicas = None
        self.basic_test(obj, state=f"0/{obj.spec.replicas} replicas exist")


class TestPersistentVolumeClaim(DriverTest):
    driver_cls = drivers.PersistentVolumeClaim
    function_name = "read_namespaced_persistent_volume_claim_status"

    def get_object(self) -> models.V1PersistentVolumeClaimStatus:
        return models.V1PersistentVolumeClaimStatus(phase="Bound")

    pvc_status = pytest.fixture(get_object)

    def test_unbound(self, pvc_status: models.V1PersistentVolumeClaimStatus) -> None:
        phase = self.fake.word()
        pvc_status.phase = phase
        self.basic_test(pvc_status, state=phase.lower())


class TestPod(DriverTest):
    driver_cls = drivers.Pod
    function_name = "read_namespaced_pod_status"

    def get_object(self) -> models.V1PodStatus:
        return models.V1PodStatus(
            conditions=[
                models.V1PodCondition(type="Ready", status="True"),
            ]
        )

    pod_status = pytest.fixture(get_object)

    def test_ready_completed(self, pod_status: models.V1PodStatus) -> None:
        pod_status.conditions = [
            models.V1PodCondition(
                type="Ready",
                status="False",
                reason="PodCompleted",
            ),
        ]
        self.basic_test(pod_status)

    def test_no_conditions(self, pod_status: models.V1PodStatus) -> None:
        pod_status.conditions = []
        self.basic_test(pod_status, state="no conditions")

    def test_no_ready_status(self, pod_status: models.V1PodStatus) -> None:
        pod_status.conditions = [
            models.V1PodCondition(
                type="Ready",
                status="False",
                reason="Bogus",
            ),
        ]
        self.basic_test(pod_status, state="not ready")


class TestPodDisruptionBudget(GenerationDriverTest):
    driver_cls = drivers.PodDisruptionBudget

    def get_object(self) -> models.V1PodDisruptionBudget:
        generation = self.fake.unique.pyint()
        healthy = self.fake.unique.pyint()
        return models.V1PodDisruptionBudget(
            metadata=models.V1ObjectMeta(generation=generation),
            status=models.V1PodDisruptionBudgetStatus(
                observed_generation=generation,
                current_healthy=healthy,
                desired_healthy=healthy,
                disruptions_allowed=0,
                expected_pods=healthy,
            ),
        )

    pod_disruption_budget = pytest.fixture(get_object)

    def test_ready_extra_healthy(
        self, pod_disruption_budget: models.V1PodDisruptionBudget
    ) -> None:
        pod_disruption_budget.status.current_healthy += 1
        self.basic_test(pod_disruption_budget)

    def test_unhealthy(
        self, pod_disruption_budget: models.V1PodDisruptionBudget
    ) -> None:
        pod_disruption_budget.status.current_healthy -= 1
        self.basic_test(pod_disruption_budget, state_contains="healthy pods")


class TestReplicaSet(ReplicasDriverTest):
    driver_cls = drivers.ReplicaSet

    def get_object(self) -> models.V1ReplicaSet:
        generation = self.fake.unique.pyint()
        replicas = self.fake.unique.pyint()
        return models.V1ReplicaSet(
            metadata=models.V1ObjectMeta(generation=generation),
            status=models.V1ReplicaSetStatus(
                observed_generation=generation,
                replicas=replicas,
                ready_replicas=replicas,
                available_replicas=replicas,
                conditions=[],
            ),
            spec=models.V1ReplicaSetSpec(
                replicas=replicas,
                selector=models.V1LabelSelector(),
            ),
        )

    replica_set = pytest.fixture(get_object)

    def test_failure_condition(self, replica_set: models.V1ReplicaSet) -> None:
        replica_set.status.conditions.append(
            models.V1ReplicaSetCondition(
                type="ReplicaFailure",
                status="True",
            )
        )
        self.basic_test(replica_set, state="replica failure")


class TestReplicationController(ReplicasDriverTest):
    driver_cls = drivers.ReplicationController

    def get_object(self) -> models.V1ReplicationController:
        generation = self.fake.unique.pyint()
        replicas = self.fake.unique.pyint()
        return models.V1ReplicationController(
            metadata=models.V1ObjectMeta(generation=generation),
            status=models.V1ReplicationControllerStatus(
                observed_generation=generation,
                replicas=replicas,
                ready_replicas=replicas,
                available_replicas=replicas,
            ),
            spec=models.V1ReplicationControllerSpec(
                replicas=replicas,
            ),
        )


class TestService(DriverTest):
    driver_cls = drivers.Service

    def get_object(self) -> models.V1Service:
        return models.V1Service(
            spec=models.V1ServiceSpec(type="ClusterIP"),
            status=models.V1ServiceStatus(),
        )

    service = pytest.fixture(get_object)

    @pytest.mark.parametrize(
        "service_type",
        [
            "ClusterIP",
            "NodePort",
            "ExternalName",
        ],
    )
    def test_ready_all_types(
        self, service: models.V1Service, service_type: str
    ) -> None:
        service.spec.type = service_type
        self.basic_test(service)

    def test_loadbalancer_ready(self, service: models.V1Service) -> None:
        service.spec.type = "LoadBalancer"
        service.spec.cluster_ip = self.fake.ipv4()
        service.status = models.V1ServiceStatus(
            load_balancer=models.V1LoadBalancerStatus(
                ingress=[models.V1LoadBalancerIngress(ip=self.fake.ipv4())]
            )
        )
        self.basic_test(service)

    def test_loadbalancer_empty_ingress_ip(self, service: models.V1Service) -> None:
        service.spec.type = "LoadBalancer"
        service.spec.cluster_ip = self.fake.ipv4()
        service.status = models.V1ServiceStatus(
            load_balancer=models.V1LoadBalancerStatus(
                ingress=[models.V1LoadBalancerIngress()]
            )
        )
        self.basic_test(service, state="no Ingress IP")

    def test_loadbalancer_no_ingress(self, service: models.V1Service) -> None:
        service.spec.type = "LoadBalancer"
        service.spec.cluster_ip = self.fake.ipv4()
        service.status = models.V1ServiceStatus(
            load_balancer=models.V1LoadBalancerStatus()
        )
        self.basic_test(service, state="no Ingress")

    def test_loadbalancer_no_cluster_ip(self, service: models.V1Service) -> None:
        service.spec.type = "LoadBalancer"
        service.spec.cluster_ip = None
        service.status = models.V1ServiceStatus(
            load_balancer=models.V1LoadBalancerStatus(
                ingress=[models.V1LoadBalancerIngress(ip=self.fake.ipv4())]
            )
        )
        self.basic_test(service, state="no IP")


class TestStatefulSet(ReplicasDriverTest):
    driver_cls = drivers.StatefulSet

    def get_object(self) -> models.V1StatefulSet:
        generation = self.fake.unique.pyint()
        replicas = self.fake.unique.pyint()
        return models.V1StatefulSet(
            metadata=models.V1ObjectMeta(generation=generation),
            status=models.V1StatefulSetStatus(
                observed_generation=generation,
                replicas=replicas,
                ready_replicas=replicas,
                available_replicas=replicas,
            ),
            spec=models.V1StatefulSetSpec(
                replicas=replicas,
                selector=models.V1LabelSelector(),
                service_name=self.fake.word(),
                template=models.V1PodTemplateSpec(),
            ),
        )
