"""Auth payload models and builders for the web UI."""
from __future__ import annotations

import yaml
from fastapi import HTTPException
from pydantic import BaseModel

from ..operations import k8s_ops, openstack_ops


class K8sLoginPayload(BaseModel):
    mode: str = "token"
    server: str | None = None
    token: str | None = None
    skip_tls_verify: bool = False
    ca_cert: str | None = None
    client_cert: str | None = None
    client_key: str | None = None
    kubeconfig_yaml: str | None = None
    context: str | None = None


class OpenStackLoginPayload(BaseModel):
    mode: str = "password"
    auth_url: str | None = None
    username: str | None = None
    password: str | None = None
    project_name: str | None = None
    user_domain_name: str = "Default"
    project_domain_name: str = "Default"
    region_name: str | None = None
    interface: str | None = None
    skip_tls_verify: bool = False
    application_credential_id: str | None = None
    application_credential_secret: str | None = None
    clouds_yaml: str | None = None
    cloud_name: str | None = None


class LoginPayload(BaseModel):
    kubernetes: K8sLoginPayload
    openstack: OpenStackLoginPayload | None = None


def _require(value: str | None, label: str) -> str:
    result = (value or "").strip()
    if not result:
        raise HTTPException(status_code=400, detail=f"{label} is required")
    return result


def _parse_yaml_document(source: str, label: str) -> dict:
    try:
        data = yaml.safe_load(source) or {}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid {label}: {exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: expected a YAML mapping")
    return data


def _validate_supported_kubeconfig(kubeconfig: dict, context_name: str | None) -> None:
    contexts = {item.get("name"): item.get("context", {}) for item in kubeconfig.get("contexts", [])}
    if not contexts:
        raise HTTPException(status_code=400, detail="Invalid kubeconfig: no contexts defined")
    active_context = context_name or kubeconfig.get("current-context") or next(iter(contexts))
    context = contexts.get(active_context)
    if not isinstance(context, dict):
        raise HTTPException(status_code=400, detail=f"Invalid kubeconfig: context {active_context!r} not found")

    clusters = {item.get("name"): item.get("cluster", {}) for item in kubeconfig.get("clusters", [])}
    users = {item.get("name"): item.get("user", {}) for item in kubeconfig.get("users", [])}
    cluster = clusters.get(context.get("cluster"))
    user = users.get(context.get("user"))
    if not isinstance(cluster, dict) or not cluster.get("server"):
        raise HTTPException(status_code=400, detail="Invalid kubeconfig: selected context has no cluster server")
    if not isinstance(user, dict):
        raise HTTPException(status_code=400, detail="Invalid kubeconfig: selected context has no user")
    if user.get("exec") or user.get("auth-provider"):
        raise HTTPException(
            status_code=400,
            detail="Unsupported kubeconfig: exec/auth-provider plugins are not supported in the web UI",
        )
    unsupported_paths = [
        cluster.get("certificate-authority"),
        user.get("client-certificate"),
        user.get("client-key"),
        user.get("tokenFile"),
    ]
    if any(unsupported_paths):
        raise HTTPException(
            status_code=400,
            detail="Unsupported kubeconfig: local file references are not supported; use inline data or upload certificates directly",
        )
    has_token = bool(user.get("token"))
    has_client_cert = bool(user.get("client-certificate-data")) and bool(user.get("client-key-data"))
    if not has_token and not has_client_cert:
        raise HTTPException(
            status_code=400,
            detail="Unsupported kubeconfig: selected user must contain an inline token or inline client certificate/key",
        )


def _build_k8s_auth(payload: K8sLoginPayload) -> k8s_ops.K8sAuth:
    mode = (payload.mode or "token").strip().lower()
    if mode == "token":
        return k8s_ops.K8sAuth(
            mode="token",
            server=_require(payload.server, "Kubernetes API server URL"),
            token=_require(payload.token, "Kubernetes bearer token"),
            skip_tls_verify=payload.skip_tls_verify,
            ca_cert=(payload.ca_cert or "").strip() or None,
        )
    if mode == "client_cert":
        return k8s_ops.K8sAuth(
            mode="client_cert",
            server=_require(payload.server, "Kubernetes API server URL"),
            skip_tls_verify=payload.skip_tls_verify,
            ca_cert=(payload.ca_cert or "").strip() or None,
            client_cert=_require(payload.client_cert, "Kubernetes client certificate"),
            client_key=_require(payload.client_key, "Kubernetes client key"),
        )
    if mode == "kubeconfig":
        kubeconfig = _parse_yaml_document(
            _require(payload.kubeconfig_yaml, "Kubeconfig"),
            "kubeconfig",
        )
        context_name = (payload.context or "").strip() or None
        _validate_supported_kubeconfig(kubeconfig, context_name)
        return k8s_ops.K8sAuth(mode="kubeconfig", kubeconfig=kubeconfig, context=context_name)
    raise HTTPException(status_code=400, detail=f"Unsupported Kubernetes auth mode: {mode}")


def _build_openstack_auth(payload: OpenStackLoginPayload) -> openstack_ops.OpenStackAuth:
    mode = (payload.mode or "password").strip().lower()
    if mode == "password":
        return openstack_ops.OpenStackAuth(
            mode="password",
            auth_url=_require(payload.auth_url, "OpenStack auth URL"),
            username=_require(payload.username, "OpenStack username"),
            password=_require(payload.password, "OpenStack password"),
            project_name=_require(payload.project_name, "OpenStack project name"),
            user_domain_name=(payload.user_domain_name or "").strip() or "Default",
            project_domain_name=(payload.project_domain_name or "").strip() or "Default",
            region_name=(payload.region_name or "").strip() or None,
            interface=(payload.interface or "").strip() or None,
            skip_tls_verify=payload.skip_tls_verify,
        )
    if mode == "application_credential":
        return openstack_ops.OpenStackAuth(
            mode="application_credential",
            auth_url=_require(payload.auth_url, "OpenStack auth URL"),
            application_credential_id=_require(
                payload.application_credential_id,
                "OpenStack application credential ID",
            ),
            application_credential_secret=_require(
                payload.application_credential_secret,
                "OpenStack application credential secret",
            ),
            region_name=(payload.region_name or "").strip() or None,
            interface=(payload.interface or "").strip() or None,
            skip_tls_verify=payload.skip_tls_verify,
        )
    if mode == "clouds_yaml":
        config_data = _parse_yaml_document(
            _require(payload.clouds_yaml, "clouds.yaml"),
            "clouds.yaml",
        )
        return _build_openstack_auth_from_clouds_yaml(config_data, payload.cloud_name)
    raise HTTPException(status_code=400, detail=f"Unsupported OpenStack auth mode: {mode}")


def _build_openstack_auth_from_clouds_yaml(
    config_data: dict,
    cloud_name: str | None,
) -> openstack_ops.OpenStackAuth:
    clouds = config_data.get("clouds")
    if not isinstance(clouds, dict) or not clouds:
        raise HTTPException(status_code=400, detail="Invalid clouds.yaml: no clouds mapping found")

    selected_cloud = (cloud_name or "").strip()
    if not selected_cloud:
        if len(clouds) != 1:
            raise HTTPException(
                status_code=400,
                detail="clouds.yaml contains multiple clouds; specify a cloud name",
            )
        selected_cloud = next(iter(clouds))

    cloud = clouds.get(selected_cloud)
    if not isinstance(cloud, dict):
        raise HTTPException(status_code=400, detail=f"clouds.yaml cloud {selected_cloud!r} not found")

    auth = cloud.get("auth")
    if not isinstance(auth, dict):
        raise HTTPException(status_code=400, detail="Invalid clouds.yaml: selected cloud has no auth section")

    region_name = str(cloud.get("region_name", "")).strip() or None
    interface = str(cloud.get("interface", "")).strip() or None
    skip_tls_verify = cloud.get("verify") is False
    if auth.get("application_credential_id") and auth.get("application_credential_secret"):
        return openstack_ops.OpenStackAuth(
            mode="application_credential",
            auth_url=_require(str(auth.get("auth_url", "")), "OpenStack auth URL"),
            application_credential_id=_require(
                str(auth.get("application_credential_id", "")),
                "OpenStack application credential ID",
            ),
            application_credential_secret=_require(
                str(auth.get("application_credential_secret", "")),
                "OpenStack application credential secret",
            ),
            region_name=region_name,
            interface=interface,
            skip_tls_verify=skip_tls_verify,
        )

    return openstack_ops.OpenStackAuth(
        mode="password",
        auth_url=_require(str(auth.get("auth_url", "")), "OpenStack auth URL"),
        username=_require(str(auth.get("username", "")), "OpenStack username"),
        password=str(auth.get("password", "")),
        project_name=_require(str(auth.get("project_name", "")), "OpenStack project name"),
        user_domain_name=str(auth.get("user_domain_name", "Default")).strip() or "Default",
        project_domain_name=str(auth.get("project_domain_name", "Default")).strip() or "Default",
        region_name=region_name,
        interface=interface,
        skip_tls_verify=skip_tls_verify,
    )


def _openstack_payload_has_credentials(payload: OpenStackLoginPayload | None) -> bool:
    if payload is None:
        return False
    candidates = [
        payload.auth_url,
        payload.username,
        payload.password,
        payload.project_name,
        payload.application_credential_id,
        payload.application_credential_secret,
        payload.clouds_yaml,
        payload.cloud_name,
    ]
    return any(str(value or "").strip() for value in candidates)
