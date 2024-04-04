"""Resource drivers and interface.

Heavily inspired by
https://github.com/GoogleCloudPlatform/cloud-builders/blob/746f95b31b8caeac2030cab569aaf824f2b9d7db/gke-deploy/core/resource/ready.go
"""

import abc
import logging
from typing import Any

import kubernetes

from kwait import inventory
from kwait import wait

_LOG = logging.getLogger(__name__)


class BaseDriver(abc.ABC):
    """Base driver interface that driver classes must inherit from."""

    api_version: str
    kind: str

    def __init__(
        self,
        client: kubernetes.client.ApiClient,
        resource: inventory.ResourceDescriptor,
    ) -> None:
        self._client = client
        self.resource = resource

    @abc.abstractmethod
    def is_ready(self) -> wait.ReadyResult:
        """Determine if this resource is "ready," whatever that means.

        Drivers must override is_ready() with an implementation that
        makes sense for the resource type.
        """

    def check_generation(self, obj: Any) -> wait.ReadyResult | None:
        """Check that object generation is correct.

        This ensures that the observed generation in status matches
        the desired generation in metadata. If it does match, returns
        `None`, otherwise, returns a `kwait.wait.ReadyResult` with
        `is_ready = False`. The usual way to use this function is:

        ```python
        if (result := self.check_generation(myobj)) is not None:
            return result
        ```
        """
        if obj.metadata.generation != obj.status.observed_generation:
            _LOG.debug(
                "%s has wrong generation: actual %s != observed %s",
                self.resource,
                obj.metadata.generation,
                obj.status.observed_generation,
            )
            return self.not_ready("wrong generation")
        return None

    def check_replica_count(self, obj: Any) -> wait.ReadyResult | None:
        """Ensure that all replicas are available.

        This performs three checks, in order, that are relevant to
        several Kubernetes objects (Deployments, StatefulSets, etc.):

        * Actual replicas (from status) == desired replicas (in spec)
        * Ready replicas (from status) == desired replicas (in spec)
        * Available replicas (from status) == desired replicas (in spec)

         If all replicas are available, returns `None`, otherwise,
        returns a `kwait.wait.ReadyResult` with `is_ready =
        False`. The usual way to use this function is:

        ```python
        if (result := self.check_replica_count(deployment)) is not None:
            return result
        ```
        """
        if obj.status.replicas != obj.spec.replicas:
            _LOG.debug(
                "%s has %s/%s replicas",
                self.resource,
                obj.status.replicas,
                obj.spec.replicas,
            )
            return self.not_ready(
                f"{obj.status.replicas}/{obj.spec.replicas} replicas exist",
            )

        if obj.status.ready_replicas != obj.spec.replicas:
            _LOG.debug(
                "%s has %s/%s replicas ready",
                self.resource,
                obj.status.ready_replicas,
                obj.spec.replicas,
            )
            return self.not_ready(
                f"{obj.status.ready_replicas}/{obj.spec.replicas} replicas ready",
            )

        if obj.status.available_replicas != obj.spec.replicas:
            _LOG.debug(
                "%s has %s/%s replicas available",
                self.resource,
                obj.status.available_replicas,
                obj.spec.replicas,
            )
            return self.not_ready(
                f"{obj.status.available_replicas}/{obj.spec.replicas} replicas available",
            )

        return None

    @property
    def ready(self) -> wait.ReadyResult:
        """Helper to get a "ready" status.

        Returns a `kwait.wait.ReadyResult` for the resource this
        driver handles with `is_ready = True` and the message set to
        `"ready"`.
        """
        return wait.ReadyResult(self.resource, True, "ready")

    def not_ready(self, message: str) -> wait.ReadyResult:
        """Helper function to get a "not ready" status.

        Returns a `kwait.wait.ReadyResult` for the resource this
        driver handles with `is_ready = False`.
        """
        return wait.ReadyResult(self.resource, False, message)


class CoreV1Driver(BaseDriver):
    """Base driver for objects that use the v1 API."""

    api_version = "v1"

    def __init__(
        self,
        client: kubernetes.client.api_client.ApiClient,
        resource: inventory.ResourceDescriptor,
    ) -> None:
        super().__init__(client, resource)
        self.corev1 = kubernetes.client.CoreV1Api(self._client)


class AppsV1Driver(BaseDriver):
    """Base driver for objects that use the apps/v1 API."""

    api_version = "apps/v1"

    def __init__(
        self,
        client: kubernetes.client.api_client.ApiClient,
        resource: inventory.ResourceDescriptor,
    ) -> None:
        super().__init__(client, resource)
        self.appsv1 = kubernetes.client.AppsV1Api(self._client)


class DaemonSet(AppsV1Driver):
    """Wait for DaemonSets to be ready.

    A DaemonSet is ready if:

    * status.observedGeneration == metadata.generation
    * status.numberAvailable == status.desiredNumberScheduled
    * status.numberReady == status.desiredNumberScheduled
    """

    kind = "DaemonSet"

    def is_ready(self) -> wait.ReadyResult:
        _LOG.debug("Fetching %s", self.resource)
        daemonset = self.appsv1.read_namespaced_daemon_set(
            self.resource.name, self.resource.namespace
        )
        if (result := self.check_generation(daemonset)) is not None:
            return result

        if (
            daemonset.status.desired_number_scheduled
            != daemonset.status.number_available
        ):
            _LOG.debug(
                "%s has %s/%s pods available",
                self.resource,
                daemonset.status.number_available,
                daemonset.status.desired_number_scheduled,
            )
            return self.not_ready(
                f"{daemonset.status.number_available}/{daemonset.status.desired_number_scheduled} pods available",
            )

        if daemonset.status.desired_number_scheduled != daemonset.status.number_ready:
            _LOG.debug(
                "%s has %s/%s pods ready",
                self.resource,
                daemonset.status.number_ready,
                daemonset.status.desired_number_scheduled,
            )
            return self.not_ready(
                f"{daemonset.status.number_ready}/{daemonset.status.desired_number_scheduled} pods ready",
            )

        return self.ready


class Deployment(AppsV1Driver):
    """Wait for Deployments to be ready.

    A Deployment is ready if:

    * status.observedGeneration == metadata.generation
    * status.replicas == spec.replicas
    * status.readyReplicas == spec.replicas
    * status.availableReplicas == spec.replicas
    * status.conditions is not empty
    * All items in status.conditions match any:
      - type == "Progressing" AND status == "True" AND
        reason == "NewReplicaSetAvailable"
      - type == "Available" AND status == "True"
    * No items in status.conditions match any:
      - type == "ReplicaFailure" AND status == "True"
    """

    kind = "Deployment"

    def is_ready(self) -> wait.ReadyResult:
        _LOG.debug("Fetching %s", self.resource)
        deployment = self.appsv1.read_namespaced_deployment(
            self.resource.name, self.resource.namespace
        )
        if (result := self.check_generation(deployment)) is not None:
            return result

        if (result := self.check_replica_count(deployment)) is not None:
            return result

        if not deployment.status.conditions:
            _LOG.debug("%s has no conditions", self.resource)
            return self.not_ready("no conditions")

        for condition in deployment.status.conditions:
            if condition.type == "ReplicaFailure" and condition.status == "True":
                _LOG.debug(
                    "%s has replica failure condition: %s",
                    self.resource,
                    condition.message,
                )
                return self.not_ready("replica failure")

            if (
                condition.type == "Progressing"
                and condition.status == "True"
                and condition.reason == "NewReplicaSetAvailable"
            ) or (condition.type == "Available" and condition.status == "True"):
                continue

            _LOG.debug(
                "%s has %s condition (%s): %s",
                self.resource,
                condition.type,
                condition.reason,
                condition.message,
            )
            return self.not_ready(condition.type.lower())

        return self.ready


class PersistentVolumeClaim(CoreV1Driver):
    """Wait for PersistentVolumeClaims to be ready.

    A PVC is ready if:

    * status.phase == "Bound"
    """

    kind = "PersistentVolumeClaim"

    def is_ready(self) -> wait.ReadyResult:
        _LOG.debug("Fetching %s", self.resource)
        status = self.corev1.read_namespaced_persistent_volume_claim_status(
            self.resource.name, self.resource.namespace
        )

        if status.phase != "Bound":
            _LOG.debug("%s is not bound, status=%s", self.resource, status.phase)
            return self.not_ready(status.phase.lower())

        return self.ready


class Pod(CoreV1Driver):
    """Wait for Pods to be ready.

    A Pod is ready if:

    * status.conditions contains at least one item that matches any:
      - type == "Ready" AND status == "True"
      - type == "Ready" AND reason == "PodCompleted"
    """

    kind = "Pod"

    def is_ready(self) -> wait.ReadyResult:
        _LOG.debug("Fetching %s", self.resource)
        status = self.corev1.read_namespaced_pod_status(
            self.resource.name, self.resource.namespace
        )

        if not status.conditions:
            _LOG.debug("%s has no conditions", self.resource)
            return self.not_ready("no conditions")

        for condition in status.conditions:
            if condition.type == "Ready" and (
                condition.status == "True" or condition.reason == "PodCompleted"
            ):
                return self.ready

        _LOG.debug(
            "%s has no Ready condition: %s",
            self.resource,
            [c.type for c in status.conditions],
        )
        return self.not_ready("not ready")


class PodDisruptionBudget(BaseDriver):
    """Wait for PodDisruptionBudgets to be ready.

    A PDB is ready if:

    * status.observedGeneration == metadata.generation
    * status.currentHealthy >= status.desiredHealthy
    """

    api_version = "policy/v1"
    kind = "PodDisruptionBudget"

    def __init__(
        self,
        client: kubernetes.client.api_client.ApiClient,
        resource: inventory.ResourceDescriptor,
    ) -> None:
        super().__init__(client, resource)
        self.policyv1 = kubernetes.client.PolicyV1Api(self._client)

    def is_ready(self) -> wait.ReadyResult:
        _LOG.debug("Fetching %s", self.resource)
        pdb = self.policyv1.read_namespaced_pod_disruption_budget(
            self.resource.name, self.resource.namespace
        )

        if (result := self.check_generation(pdb)) is not None:
            return result

        if pdb.status.current_healthy < pdb.status.desired_healthy:
            _LOG.debug(
                "%s has %s/%s healthy pods",
                self.resource,
                pdb.status.current_healthy,
                pdb.status.desired_healthy,
            )
            return self.not_ready(
                f"{pdb.status.current_healthy}/{pdb.status.desired_healthy} healthy pods"
            )

        return self.ready


class ReplicaSet(AppsV1Driver):
    """Wait for ReplicaSets to be ready.

    A ReplicaSet is ready if:

    * status.observedGeneration == metadata.generation
    * status.replicas == spec.replicas
    * status.readyReplicas == spec.replicas
    * status.availableReplicas == spec.replicas
    * No items in status.conditions match any:
      - type == "ReplicaFailure" AND status == "True"
    """

    kind = "ReplicaSet"

    def is_ready(self) -> wait.ReadyResult:
        _LOG.debug("Fetching %s", self.resource)
        replicaset = self.appsv1.read_namespaced_replica_set(
            self.resource.name, self.resource.namespace
        )

        if (result := self.check_generation(replicaset)) is not None:
            return result

        if (result := self.check_replica_count(replicaset)) is not None:
            return result

        for condition in replicaset.status.conditions:
            if condition.type == "ReplicaFailure" and condition.status == "True":
                _LOG.debug(
                    "%s has replica failure condition: %s",
                    self.resource,
                    condition.message,
                )
                return self.not_ready("replica failure")

        return self.ready


class ReplicationController(CoreV1Driver):
    """Wait for ReplicationControllers to be ready.

    A ReplicationController is ready if:

    * status.observedGeneration == metadata.generation
    * status.replicas == spec.replicas
    * status.readyReplicas == spec.replicas
    * status.availableReplicas == spec.replicas
    """

    kind = "ReplicationController"

    def is_ready(self) -> wait.ReadyResult:
        _LOG.debug("Fetching %s", self.resource)
        controller = self.corev1.read_namespaced_replication_controller(
            self.resource.name, self.resource.namespace
        )

        if (result := self.check_generation(controller)) is not None:
            return result

        if (result := self.check_replica_count(controller)) is not None:
            return result

        return self.ready


class Service(CoreV1Driver):
    """Wait for Services to be ready.

    A Service is ready if:

    * Any of the following are true
      - type == "ClusterIP" (default)
      - type == "NodePort"
      - type == "ExternalName"
      - type == "LoadBalancer" AND "spec.clusterIP" is not empty AND
        "status.loadBalancer.ingress" is not empty AND all objects in
        "status.loadBalancer.ingress" has an "ip" that is not empty
    """

    kind = "Service"

    def is_ready(self) -> wait.ReadyResult:
        _LOG.debug("Fetching %s", self.resource)
        service = self.corev1.read_namespaced_service(
            self.resource.name, self.resource.namespace
        )

        if service.spec.type == "LoadBalancer":
            if not service.spec.cluster_ip:
                _LOG.debug("%s has no Cluster IP", self.resource)
                return self.not_ready("no IP")

            if not service.status.load_balancer.ingress:
                _LOG.debug("%s has no Ingress", self.resource)
                return self.not_ready("no Ingress")

            for ingress in service.status.load_balancer.ingress:
                if not ingress.ip:
                    _LOG.debug(
                        "%s has at least one Ingress with no IP: %s",
                        self.resource,
                        ingress.hostname,
                    )
                    return self.not_ready("no Ingress IP")

        return self.ready


class StatefulSet(AppsV1Driver):
    """Wait for StatefulSets to be ready.

    A StatefulSet is ready if:

    * status.observedGeneration == metadata.generation
    * status.replicas == spec.replicas
    * status.readyReplicas == spec.replicas
    * status.currentReplicas == spec.replicas
    """

    kind = "StatefulSet"

    def is_ready(self) -> wait.ReadyResult:
        _LOG.debug("Fetching %s", self.resource)
        controller = self.appsv1.read_namespaced_stateful_set(
            self.resource.name, self.resource.namespace
        )

        if (result := self.check_generation(controller)) is not None:
            return result

        if (result := self.check_replica_count(controller)) is not None:
            return result

        return self.ready
