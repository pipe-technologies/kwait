"""Driver unit tests."""

import re
from typing import Any
from typing import cast
from unittest import mock

import faker
from kubernetes.client import models
import pytest

from kwait import drivers
from kwait import inventory


class DriverTest:
    """DriverTest is a helper for running tests on drivers.

    Instantiating this class gets you a callable object that can be
    used to run individual test cases against the driver class passed
    in to the constructor. It handles:

    * Creating the `ResourceDescriptor` object to test on;
    * Mocking the necessary Kubernetes client APIs;
    * Asserting the appropriate result.
    """

    _camel_case_re = re.compile(r"(?<!^)(?=[A-Z])")

    def __init__(
        self,
        driver_cls: type[drivers.BaseDriver],
        function_name: str = None,
    ) -> None:
        self.driver_cls = driver_cls
        self._fake = faker.Faker()

        if function_name:
            self.function_name = function_name
        else:
            camel_case_kind = self._camel_case_re.sub("_", self.driver_cls.kind).lower()
            self.function_name = f"read_namespaced_{camel_case_kind}"

    def __call__(
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
        resource = inventory.ResourceDescriptor(
            self.driver_cls.api_version,
            self.driver_cls.kind,
            self._fake.word(),
            self._fake.word(),
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
            getattr(mock_api.return_value, self.function_name).return_value = mock_obj

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


# pylint: disable=missing-function-docstring,redefined-outer-name


@pytest.fixture
def daemonset(fake: faker.Faker) -> models.V1DaemonSet:
    generation = fake.unique.pyint()
    ready = fake.unique.pyint()
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


_test_daemonset = DriverTest(drivers.DaemonSet)


def test_daemonset_ready(daemonset: models.V1DaemonSet) -> None:
    _test_daemonset(daemonset)


def test_daemonset_wrong_observed_generation(
    fake: faker.Faker, daemonset: models.V1DaemonSet
) -> None:
    daemonset.status.observed_generation = fake.unique.pyint()
    _test_daemonset(daemonset, state="wrong generation")


def test_daemonset_wrong_metadata_generation(
    fake: faker.Faker, daemonset: models.V1DaemonSet
) -> None:
    daemonset.metadata.generation = fake.unique.pyint()
    _test_daemonset(daemonset, state="wrong generation")


def test_daemonset_not_available(
    fake: faker.Faker, daemonset: models.V1DaemonSet
) -> None:
    daemonset.status.number_available = fake.unique.pyint()
    _test_daemonset(daemonset, state_contains="pods available")


def test_daemonset_not_ready(fake: faker.Faker, daemonset: models.V1DaemonSet) -> None:
    daemonset.status.number_ready = fake.unique.pyint()
    _test_daemonset(daemonset, state_contains="pods ready")


@pytest.fixture
def deployment(fake: faker.Faker) -> models.V1Deployment:
    generation = fake.unique.pyint()
    replicas = fake.unique.pyint()
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


_test_deployment = DriverTest(drivers.Deployment)


def test_deployment_ready(deployment: models.V1Deployment) -> None:
    _test_deployment(deployment)


def test_deployment_wrong_observed_generation(
    fake: faker.Faker, deployment: models.V1Deployment
) -> None:
    deployment.status.observed_generation = fake.unique.pyint()
    _test_deployment(deployment, state="wrong generation")


def test_deployment_wrong_metadata_generation(
    fake: faker.Faker, deployment: models.V1Deployment
) -> None:
    deployment.metadata.generation = fake.unique.pyint()
    _test_deployment(deployment, state="wrong generation")


def test_deployment_wrong_status_replicas(
    fake: faker.Faker, deployment: models.V1Deployment
) -> None:
    deployment.status.replicas = fake.unique.pyint()
    _test_deployment(deployment, state_contains="replicas exist")


def test_deployment_wrong_spec_replicas(
    fake: faker.Faker, deployment: models.V1Deployment
) -> None:
    deployment.spec.replicas = fake.unique.pyint()
    _test_deployment(deployment, state_contains="replicas exist")


def test_deployment_wrong_ready_replicas(
    fake: faker.Faker, deployment: models.V1Deployment
) -> None:
    deployment.status.ready_replicas = fake.unique.pyint()
    _test_deployment(deployment, state_contains="replicas ready")


def test_deployment_wrong_available_replicas(
    fake: faker.Faker, deployment: models.V1Deployment
) -> None:
    deployment.status.available_replicas = fake.unique.pyint()
    _test_deployment(deployment, state_contains="replicas available")


def test_deployment_unavailable_condition(
    fake: faker.Faker, deployment: models.V1Deployment
) -> None:
    condition_type = fake.word()
    deployment.status.conditions.append(
        models.V1DeploymentCondition(
            type=condition_type,
            status="False",
        )
    )
    _test_deployment(deployment, state=condition_type.lower())


def test_deployment_failure_condition(deployment: models.V1Deployment) -> None:
    deployment.status.conditions.append(
        models.V1DeploymentCondition(
            type="ReplicaFailure",
            status="True",
        )
    )
    _test_deployment(deployment, state="replica failure")


def test_deployment_no_conditions(deployment: models.V1Deployment) -> None:
    deployment.status.conditions = []
    _test_deployment(deployment, state="no conditions")


@pytest.fixture
def pvc_status() -> models.V1PersistentVolumeClaimStatus:
    return models.V1PersistentVolumeClaimStatus(phase="Bound")


_test_pvc = DriverTest(
    drivers.PersistentVolumeClaim,
    function_name="read_namespaced_persistent_volume_claim_status",
)


def test_pvc_ready(
    pvc_status: models.V1PersistentVolumeClaimStatus,
) -> None:
    _test_pvc(pvc_status)


def test_pvc_unbound(
    fake: faker.Faker,
    pvc_status: models.V1PersistentVolumeClaimStatus,
) -> None:
    phase = fake.word()
    pvc_status.phase = phase
    _test_pvc(pvc_status, state=phase.lower())


@pytest.fixture
def pod_status() -> models.V1PodStatus:
    return models.V1PodStatus(
        conditions=[
            models.V1PodCondition(type="Ready", status="True"),
        ]
    )


_test_pod = DriverTest(drivers.Pod, function_name="read_namespaced_pod_status")


def test_pod_ready(pod_status: models.V1PodStatus) -> None:
    _test_pod(pod_status)


def test_pod_ready_completed(
    pod_status: models.V1PodStatus,
) -> None:
    pod_status.conditions = [
        models.V1PodCondition(
            type="Ready",
            status="False",
            reason="PodCompleted",
        ),
    ]
    _test_pod(pod_status)


def test_pod_no_conditions(
    pod_status: models.V1PodStatus,
) -> None:
    pod_status.conditions = []
    _test_pod(pod_status, state="no conditions")


def test_pod_no_ready_status(
    pod_status: models.V1PodStatus,
) -> None:
    pod_status.conditions = [
        models.V1PodCondition(
            type="Ready",
            status="False",
            reason="Bogus",
        ),
    ]
    _test_pod(pod_status, state="not ready")


@pytest.fixture
def pod_disruption_budget(fake: faker.Faker) -> models.V1PodDisruptionBudget:
    generation = fake.unique.pyint()
    healthy = fake.unique.pyint()
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


_test_pdb = DriverTest(drivers.PodDisruptionBudget)


def test_pdb_ready(pod_disruption_budget: models.V1PodDisruptionBudget) -> None:
    _test_pdb(pod_disruption_budget)


def test_pdb_ready_extra_healthy(
    pod_disruption_budget: models.V1PodDisruptionBudget,
) -> None:
    pod_disruption_budget.status.current_healthy += 1
    _test_pdb(pod_disruption_budget)


def test_pdb_wrong_observed_generation(
    fake: faker.Faker, pod_disruption_budget: models.V1PodDisruptionBudget
) -> None:
    pod_disruption_budget.status.observed_generation = fake.unique.pyint()
    _test_pdb(pod_disruption_budget, state="wrong generation")


def test_pdb_wrong_metadata_generation(
    fake: faker.Faker, pod_disruption_budget: models.V1PodDisruptionBudget
) -> None:
    pod_disruption_budget.metadata.generation = fake.unique.pyint()
    _test_pdb(pod_disruption_budget, state="wrong generation")


def test_pdb_unhealthy(pod_disruption_budget: models.V1PodDisruptionBudget) -> None:
    pod_disruption_budget.status.current_healthy -= 1
    _test_pdb(pod_disruption_budget, state_contains="healthy pods")


@pytest.fixture
def replica_set(fake: faker.Faker) -> models.V1ReplicaSet:
    generation = fake.unique.pyint()
    replicas = fake.unique.pyint()
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


_test_replica_set = DriverTest(drivers.ReplicaSet)


def test_replica_set_ready(replica_set: models.V1ReplicaSet) -> None:
    _test_replica_set(replica_set)


def test_replica_set_wrong_observed_generation(
    fake: faker.Faker, replica_set: models.V1ReplicaSet
) -> None:
    replica_set.status.observed_generation = fake.unique.pyint()
    _test_replica_set(replica_set, state="wrong generation")


def test_replica_set_wrong_metadata_generation(
    fake: faker.Faker, replica_set: models.V1ReplicaSet
) -> None:
    replica_set.metadata.generation = fake.unique.pyint()
    _test_replica_set(replica_set, state="wrong generation")


def test_replica_set_wrong_status_replicas(
    fake: faker.Faker, replica_set: models.V1ReplicaSet
) -> None:
    replica_set.status.replicas = fake.unique.pyint()
    _test_replica_set(replica_set, state_contains="replicas exist")


def test_replica_set_wrong_spec_replicas(
    fake: faker.Faker, replica_set: models.V1ReplicaSet
) -> None:
    replica_set.spec.replicas = fake.unique.pyint()
    _test_replica_set(replica_set, state_contains="replicas exist")


def test_replica_set_wrong_ready_replicas(
    fake: faker.Faker, replica_set: models.V1ReplicaSet
) -> None:
    replica_set.status.ready_replicas = fake.unique.pyint()
    _test_replica_set(replica_set, state_contains="replicas ready")


def test_replica_set_wrong_available_replicas(
    fake: faker.Faker, replica_set: models.V1ReplicaSet
) -> None:
    replica_set.status.available_replicas = fake.unique.pyint()
    _test_replica_set(replica_set, state_contains="replicas available")


def test_replica_set_failure_condition(replica_set: models.V1ReplicaSet) -> None:
    replica_set.status.conditions.append(
        models.V1ReplicaSetCondition(
            type="ReplicaFailure",
            status="True",
        )
    )
    _test_replica_set(replica_set, state="replica failure")


@pytest.fixture
def replication_controller(fake: faker.Faker) -> models.V1ReplicationController:
    generation = fake.unique.pyint()
    replicas = fake.unique.pyint()
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


_test_replication_controller = DriverTest(drivers.ReplicationController)


def test_replication_controller_ready(
    replication_controller: models.V1ReplicationController,
) -> None:
    _test_replication_controller(replication_controller)


def test_replication_controller_wrong_observed_generation(
    fake: faker.Faker, replication_controller: models.V1ReplicationController
) -> None:
    replication_controller.status.observed_generation = fake.unique.pyint()
    _test_replication_controller(replication_controller, state="wrong generation")


def test_replication_controller_wrong_metadata_generation(
    fake: faker.Faker, replication_controller: models.V1ReplicationController
) -> None:
    replication_controller.metadata.generation = fake.unique.pyint()
    _test_replication_controller(replication_controller, state="wrong generation")


def test_replication_controller_wrong_status_replicas(
    fake: faker.Faker, replication_controller: models.V1ReplicationController
) -> None:
    replication_controller.status.replicas = fake.unique.pyint()
    _test_replication_controller(
        replication_controller, state_contains="replicas exist"
    )


def test_replication_controller_wrong_spec_replicas(
    fake: faker.Faker, replication_controller: models.V1ReplicationController
) -> None:
    replication_controller.spec.replicas = fake.unique.pyint()
    _test_replication_controller(
        replication_controller, state_contains="replicas exist"
    )


def test_replication_controller_wrong_ready_replicas(
    fake: faker.Faker, replication_controller: models.V1ReplicationController
) -> None:
    replication_controller.status.ready_replicas = fake.unique.pyint()
    _test_replication_controller(
        replication_controller, state_contains="replicas ready"
    )


def test_replication_controller_wrong_available_replicas(
    fake: faker.Faker, replication_controller: models.V1ReplicationController
) -> None:
    replication_controller.status.available_replicas = fake.unique.pyint()
    _test_replication_controller(
        replication_controller, state_contains="replicas available"
    )


@pytest.fixture
def service() -> models.V1Service:
    return models.V1Service(
        spec=models.V1ServiceSpec(type="ClusterIP"),
        status=models.V1ServiceStatus(),
    )


_test_service = DriverTest(drivers.Service)


@pytest.mark.parametrize("service_type", ["ClusterIP", "NodePort", "ExternalName"])
def test_service_ready(service: models.V1Service, service_type: str) -> None:
    service.spec.type = service_type
    _test_service(service)


def test_service_loadbalancer_ready(
    fake: faker.Faker, service: models.V1Service
) -> None:
    service.spec.type = "LoadBalancer"
    service.spec.cluster_ip = fake.ipv4()
    service.status = models.V1ServiceStatus(
        load_balancer=models.V1LoadBalancerStatus(
            ingress=[models.V1LoadBalancerIngress(ip=fake.ipv4())]
        )
    )
    _test_service(service)


def test_service_loadbalancer_empty_ingress_ip(
    fake: faker.Faker, service: models.V1Service
) -> None:
    service.spec.type = "LoadBalancer"
    service.spec.cluster_ip = fake.ipv4()
    service.status = models.V1ServiceStatus(
        load_balancer=models.V1LoadBalancerStatus(
            ingress=[models.V1LoadBalancerIngress()]
        )
    )
    _test_service(service, state="no Ingress IP")


def test_service_loadbalancer_no_ingress(
    fake: faker.Faker, service: models.V1Service
) -> None:
    service.spec.type = "LoadBalancer"
    service.spec.cluster_ip = fake.ipv4()
    service.status = models.V1ServiceStatus(load_balancer=models.V1LoadBalancerStatus())
    _test_service(service, state="no Ingress")


def test_service_loadbalancer_no_cluster_ip(
    fake: faker.Faker, service: models.V1Service
) -> None:
    service.spec.type = "LoadBalancer"
    service.spec.cluster_ip = None
    service.status = models.V1ServiceStatus(
        load_balancer=models.V1LoadBalancerStatus(
            ingress=[models.V1LoadBalancerIngress(ip=fake.ipv4())]
        )
    )
    _test_service(service, state="no IP")


@pytest.fixture
def stateful_set(fake: faker.Faker) -> models.V1StatefulSet:
    generation = fake.unique.pyint()
    replicas = fake.unique.pyint()
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
            service_name=fake.word(),
            template=models.V1PodTemplateSpec(),
        ),
    )


_test_stateful_set = DriverTest(drivers.StatefulSet)


def test_stateful_set_ready(stateful_set: models.V1StatefulSet) -> None:
    _test_stateful_set(stateful_set)


def test_stateful_set_wrong_observed_generation(
    fake: faker.Faker, stateful_set: models.V1StatefulSet
) -> None:
    stateful_set.status.observed_generation = fake.unique.pyint()
    _test_stateful_set(stateful_set, state="wrong generation")


def test_stateful_set_wrong_metadata_generation(
    fake: faker.Faker, stateful_set: models.V1StatefulSet
) -> None:
    stateful_set.metadata.generation = fake.unique.pyint()
    _test_stateful_set(stateful_set, state="wrong generation")


def test_stateful_set_wrong_status_replicas(
    fake: faker.Faker, stateful_set: models.V1StatefulSet
) -> None:
    stateful_set.status.replicas = fake.unique.pyint()
    _test_stateful_set(stateful_set, state_contains="replicas exist")


def test_stateful_set_wrong_spec_replicas(
    fake: faker.Faker, stateful_set: models.V1StatefulSet
) -> None:
    stateful_set.spec.replicas = fake.unique.pyint()
    _test_stateful_set(stateful_set, state_contains="replicas exist")


def test_stateful_set_wrong_ready_replicas(
    fake: faker.Faker, stateful_set: models.V1StatefulSet
) -> None:
    stateful_set.status.ready_replicas = fake.unique.pyint()
    _test_stateful_set(stateful_set, state_contains="replicas ready")


def test_stateful_set_wrong_available_replicas(
    fake: faker.Faker, stateful_set: models.V1StatefulSet
) -> None:
    cast(models.V1StatefulSetStatus, stateful_set.status).available_replicas = (
        fake.unique.pyint()
    )
    _test_stateful_set(stateful_set, state_contains="replicas available")
