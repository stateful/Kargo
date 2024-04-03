import pulumi
from pulumi_kubernetes import helm, Provider

import pulumi_kubernetes as k8s
from pulumi_kubernetes.apiextensions.CustomResource import CustomResource
from ...lib.helm_chart_versions import get_latest_helm_chart_version
import json
import secrets
import base64
from kubernetes import config, dynamic
from kubernetes import client as k8s_client
from kubernetes.dynamic.exceptions import ResourceNotFoundError
from kubernetes.client import api_client


def deploy_openunison(name: str, k8s_provider: Provider, kubernetes_distribution: str, project_name: str, namespace: str):
    # Initialize Pulumi configuration
    pconfig = pulumi.Config()

    # Deploy the Kubernetes Dashboard 6.0.8
    k8s_db_release = deploy_kubernetes_dashboard(name=name,k8s_provider=k8s_provider,kubernetes_distribution=kubernetes_distribution,project_name=project_name,namespace=namespace)

    # generate openunison namespace
    openunison_namespace = k8s.core.v1.Namespace("openunison",
            metadata= k8s.meta.v1.ObjectMetaArgs(
                name="openunison"
            ),
            opts=pulumi.ResourceOptions(
                provider = k8s_provider,
                retain_on_delete=True,
                custom_timeouts=pulumi.CustomTimeouts(
                    create="10m",
                    update="10m",
                    delete="10m"
                )
            )
        )

    # get the domain suffix and cluster_issuer
    domain_suffix = pconfig.require('openunison.dns_suffix')

    # get the cluster issuer
    cluster_issuer = pconfig.require('openunison.cluster_issuer')

    # create the Certificate
    openunison_certificate = CustomResource(
        "ou-tls-certificate",
        api_version="cert-manager.io/v1",
        kind="Certificate",
        metadata={
            "name": "ou-tls-certificate",
            "namespace": "openunison",
        },
        spec={
            "secretName": "ou-tls-certificate",
            "commonName": "*." + domain_suffix,
            "isCA": False,
            "privateKey": {
                "algorithm": "RSA",
                "encoding": "PKCS1",
                "size": 2048,
            },
            "usages": ["server auth","client auth"],
            "dnsNames": ["*." + domain_suffix, domain_suffix],
            "issuerRef": {
                "name": cluster_issuer,
                "kind": "ClusterIssuer",
                "group": "cert-manager.io",
            },
        },
        opts=pulumi.ResourceOptions(
            provider = k8s_provider,
            depends_on=[openunison_namespace],
            custom_timeouts=pulumi.CustomTimeouts(
                create="5m",
                update="10m",
                delete="10m"
            )
        )
    )

    # this is probably the wrong way to do this, but <shrug>
    config.load_kube_config()


    cluster_issuer_object = k8s_client.CustomObjectsApi().get_cluster_custom_object(group="cert-manager.io",version="v1",plural="clusterissuers",name=cluster_issuer)

    cluster_issuer_ca_secret_name = cluster_issuer_object["spec"]["ca"]["secretName"]

    pulumi.log.info("Loading CA from {}".format(cluster_issuer_ca_secret_name))

    ca_secret = k8s_client.CoreV1Api().read_namespaced_secret(namespace="cert-manager",name=cluster_issuer_ca_secret_name)

    ca_cert = ca_secret.data["tls.crt"]

    pulumi.log.info("CA Certificate {}".format(ca_cert))

    deploy_openunison_charts(ca_cert=ca_cert,k8s_provider=k8s_provider,kubernetes_distribution=kubernetes_distribution,project_name=project_name,namespace=namespace,domain_suffix=domain_suffix,openunison_certificate=openunison_certificate,config=pconfig,db_release=k8s_db_release)


    # get the cluster issuer
    # cluster_issuer = CustomResource.get(resource_name="openunison_cluster_issuer",id=cluster_issuer,api_version="cert-manager.io/v1",kind="ClusterIssuer",opts=pulumi.ResourceOptions(
    #                                                 provider = k8s_provider,
    #                                                 depends_on=[],
    #                                                 custom_timeouts=pulumi.CustomTimeouts(
    #                                                     create="5m",
    #                                                     update="10m",
    #                                                     delete="10m"
    #                                                 )
    #                                             ))

    # pulumi.export("openunison_cluster_issuer",cluster_issuer)

    # # get the CA certificate from the issued cert
    # cert_ca = k8s.core.v1.Secret.get("ou-tls-certificate",cluster_issuer.spec.ca.secretName.apply(lambda secretName: "cert-manager/" + secretName),
    #                                         opts=pulumi.ResourceOptions(
    #                                             provider = k8s_provider,
    #                                             depends_on=[],
    #                                             custom_timeouts=pulumi.CustomTimeouts(
    #                                                 create="5m",
    #                                                 update="10m",
    #                                                 delete="10m"
    #                                             )
    #                                         )).data["tls.crt"].apply(lambda data: data  )



    # deploy_openunison_charts(ca_cert=cert_ca,k8s_provider=k8s_provider,kubernetes_distribution=kubernetes_distribution,project_name=project_name,namespace=namespace,domain_suffix=domain_suffix,openunison_certificate=openunison_certificate,config=pconfig,db_release=k8s_db_release)


def deploy_openunison_charts(ca_cert,k8s_provider: Provider, kubernetes_distribution: str, project_name: str, namespace: str,domain_suffix: str,openunison_certificate,config,db_release):
    prometheus_enabled = config.get_bool("prometheus.enabled") or False
    kubevirt_manager_enabled = config.get_bool("kubevirt_manager.enabled") or False

    openunison_helm_values = {
        "enable_wait_for_job": True,
        "network": {
            "openunison_host": "k8sou." + domain_suffix,
            "dashboard_host": "k8sdb." + domain_suffix,
            "api_server_host": "k8sapi." + domain_suffix,
            "session_inactivity_timeout_seconds": 900,
            "k8s_url": "https://192.168.2.130:6443",
            "force_redirect_to_tls": False,
            "createIngressCertificate": False,
            "ingress_type": "nginx",
            "ingress_annotations": {
            }
        },
        "cert_template": {
            "ou": "Kubernetes",
            "o": "MyOrg",
            "l": "My Cluster",
            "st": "State of Cluster",
            "c": "MyCountry"
        },
        "myvd_config_path": "WEB-INF/myvd.conf",
        "k8s_cluster_name": "openunison-kargo",
        "enable_impersonation": True,
        "impersonation": {
            "use_jetstack": True,
            "explicit_certificate_trust": True
        },
        "dashboard": {
            "namespace": "kubernetes-dashboard",
            #"cert_name": "kubernetes-dashboard-certs",
            "label": "app.kubernetes.io/name=kubernetes-dashboard",
            #"service_name": db_release.name.apply(lambda name: "kubernetes-dashboard-" + name)   ,
            "require_session": True
        },
        "certs": {
            "use_k8s_cm": False
        },
        "trusted_certs": [
        {
            "name": "unison-ca",
            "pem_b64": ca_cert,
        }
        ],
        "monitoring": {
            "prometheus_service_account": "system:serviceaccount:monitoring:prometheus-k8s"
        },
        "github": {
            "client_id": config.require('openunison.github.client_id'),
            "teams": config.require('openunison.github.teams'),
        },
        "network_policies": {
        "enabled": False,
        "ingress": {
            "enabled": True,
            "labels": {
            "app.kubernetes.io/name": "ingress-nginx"
            }
        },
        "monitoring": {
            "enabled": True,
            "labels": {
            "app.kubernetes.io/name": "monitoring"
            }
        },
        "apiserver": {
            "enabled": False,
            "labels": {
            "app.kubernetes.io/name": "kube-system"
            }
        }
        },
        "services": {
        "enable_tokenrequest": False,
        "token_request_audience": "api",
        "token_request_expiration_seconds": 600,
        "node_selectors": [

        ]
        },
        "openunison": {
        "replicas": 1,
        "non_secret_data": {
            "K8S_DB_SSO": "oidc",
            "PROMETHEUS_SERVICE_ACCOUNT": "system:serviceaccount:monitoring:prometheus-k8s",
            "SHOW_PORTAL_ORGS": "False"
        },
        "secrets": [

        ],
        "enable_provisioning": False,
        "use_standard_jit_workflow": True,
        "apps":[],
        }
    }

    # now that OpenUnison is deployed, we'll make ClusterAdmins of all the groups specified in openunison.github.teams
    github_teams = config.require('openunison.github.teams').split(',')
    subjects = []
    az_groups = []
    for team in github_teams:
        team = team.strip()
        if team.endswith('/'):
            team = team[:-1]

        subject = k8s.rbac.v1.SubjectArgs(
            kind="Group",
            api_group="rbac.authorization.k8s.io",
            name=team
        )
        subjects.append(subject)
        az_groups.append(team)

    if kubevirt_manager_enabled:
        openunison_helm_values["openunison"]["apps"].append(
                    {
                        "name": "kubevirt-manager",
                        "label": "KubeVirt Manager",
                        "org": "b1bf4c92-7220-4ad2-91af-ee0fe0af7312",
                        "badgeUrl": "https://kubeverit-manager." + domain_suffix + "/",
                        "injectToken": False,
                        "proxyTo": "http://kubevirt-manager.kubevirt-manager.svc:8080${fullURI}",
                        "az_groups": az_groups,
                        "icon": "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JH7kuurxG+eiywLPFM1Mep5YJJaKXax2MSuZOvEUcVTTDcoXsh5rnLc465U6a9+TvzCSN1aWuU5rBEksYgkyJKioo4wKbMRpN0ixkKbzhI9/2PXL5FLJVQYjxwKq0KG4fvA/+N1bqzA54SVFEkDPi+N8xIDwLtBqOM73seO0ToDgM3BldPzVJjDzSXqjo0WPgIFt4OK6o6l7wOUOMPRUU0zFlYK0hEIBeD+jb8oBg7dA35rXt/Y5Th+ADPUqdQMcHAKjRcpe93l3b3ff/q1p9+8HRelylYisUsIAAA5eaVRYdFhNTDpjb20uYWRvYmUueG1wAAAAAAA8P3hwYWNrZXQgYmVnaW49Iu+7vyIgaWQ9Ilc1TTBNcENlaGlIenJlU3pOVGN6a2M5ZCI/Pgo8eDp4bXBtZXRhIHhtbG5zOng9ImFkb2JlOm5zOm1ldGEvIiB4OnhtcHRrPSJYTVAgQ29yZSA0LjQuMC1FeGl2MiI+CiA8cmRmOlJERiB4bWxuczpyZGY9Imh0dHA6Ly93d3cudzMub3JnLzE5OTkvMDIvMjItcmRmLXN5bnRheC1ucyMiPgogIDxyZGY6RGVzY3JpcHRpb24gcmRmOmFib3V0PSIiCiAgICB4bWxuczp4bXBNTT0iaHR0cDovL25zLmFkb2JlLmNvbS94YXAvMS4wL21tLyIKICAgIHhtbG5zOnN0RXZ0PSJodHRwOi8vbnMuYWRvYmUuY29tL3hhcC8xLjAvc1R5cGUvUmVzb3VyY2VFdmVudCMiCiAgICB4bWxuczpkYz0iaHR0cDovL3B1cmwub3JnL2RjL2VsZW1lbnRzLzEuMS8iCiAgICB4bWxuczpHSU1QPSJodHRwOi8vd3d3LmdpbXAub3JnL3htcC8iCiAgICB4bWxuczp0aWZmPSJodHRwOi8vbnMuYWRvYmUuY29tL3RpZmYvMS4wLyIKICAgIHhtbG5zOnhtcD0iaHR0cDovL25zLmFkb2JlLmNvbS94YXAvMS4wLyIKICAgeG1wTU06RG9jdW1lbnRJRD0iZ2ltcDpkb2NpZDpnaW1wOjExN2U0YjM2LTliMGUtNGFkMy1hNjg5LTg5MWFkNmM5NjBjOSIKICAgeG1wTU06SW5zdGFuY2VJRD0ieG1wLmlpZDo2NmY2YmU2Yy0zMGFjLTRhZTktOWRmZC0wOGJhYWZiY2E4OTUiCiAgIHhtcE1NOk9yaWdpbmFsRG9jdW1lbnRJRD0ieG1wLmRpZDoxOWM3YjM1Yy02ZGJlLTRkODgtOTM3My05OWYyMzk2MTgxY2QiCiAgIGRjOkZvcm1hdD0iaW1hZ2UvcG5nIgogICBHSU1QOkFQST0iMi4wIgogICBHSU1QOlBsYXRmb3JtPSJNYWMgT1MiCiAgIEdJTVA6VGltZVN0YW1wPSIxNzEyMTc2MjQzOTEyOTE5IgogICBHSU1QOlZlcnNpb249IjIuMTAuMjgiCiAgIHRpZmY6T3JpZW50YXRpb249IjEiCiAgIHhtcDpDcmVhdG9yVG9vbD0iR0lNUCAyLjEwIgogICB4bXA6TWV0YWRhdGFEYXRlPSIyMDIyOjExOjI5VDA5OjI1OjIzLTAzOjAwIgogICB4bXA6TW9kaWZ5RGF0ZT0iMjAyMjoxMToyOVQwOToyNToyMy0wMzowMCI+CiAgIDx4bXBNTTpIaXN0b3J5PgogICAgPHJkZjpTZXE+CiAgICAgPHJkZjpsaQogICAgICBzdEV2dDphY3Rpb249InNhdmVkIgogICAgICBzdEV2dDpjaGFuZ2VkPSIvIgogICAgICBzdEV2dDppbnN0YW5jZUlEPSJ4bXAuaWlkOjgzYjM5ZjEwLTE4N2MtNDEyOS05OThiLTkwZmE2ZmQ3MTEyZiIKICAgICAgc3RFdnQ6c29mdHdhcmVBZ2VudD0iR2ltcCAyLjEwIChNYWMgT1MpIgogICAgICBzdEV2dDp3aGVuPSIyMDIyLTExLTI5VDA5OjI1OjI5LTAzOjAwIi8+CiAgICAgPHJkZjpsaQogICAgICBzdEV2dDphY3Rpb249InNhdmVkIgogICAgICBzdEV2dDpjaGFuZ2VkPSIvIgogICAgICBzdEV2dDppbnN0YW5jZUlEPSJ4bXAuaWlkOjg5ZjI3MmZkLTAzNjItNGE1MC05YzFiLTJjMjM0NDQyNzhmMiIKICAgICAgc3RFdnQ6c29mdHdhcmVBZ2VudD0iR2ltcCAyLjEwIChNYWMgT1MpIgogICAgICBzdEV2dDp3aGVuPSIyMDI0LTA0LTAzVDE2OjMwOjQzLTA0OjAwIi8+CiAgICA8L3JkZjpTZXE+CiAgIDwveG1wTU06SGlzdG9yeT4KICA8L3JkZjpEZXNjcmlwdGlvbj4KIDwvcmRmOlJERj4KPC94OnhtcG1ldGE+CiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAKPD94cGFja2V0IGVuZD0idyI/PtxdAmgAAAAPUExURQCtte7u7r3h4nbN0TC6wRdJ5pcAAAABYktHRACIBR1IAAAACXBIWXMAAAsTAAALEwEAmpwYAAAAB3RJTUUH6AQDFB4rsiT3UgAABLhJREFUeNrtnGuSmzAMx5k1ByjQA4SEAwCTA1DY+5+pWDav8LItyTPtSh8yHbbhB/bfkvxQkkRMTExMTExMTExMTExMTCyOpc8uEqjM8jioJsuyIgaozbS9+EF9ZqyKBcqymhekZlCWs6JUCZAnOyotrRqMKvi0njaz7Ayq6Fj1DW8yvx2j7GyTWVTFCKq34qjY9L3obWAaVgev0LNo/bCtegatn0jNap1+IO2Gz9l1koHk9K5U+r7tPw7QjHoTDqSzew1kWr91O1Rad+hzohDiouOGYlg5PS+F1h0bBo9y9qFYrSv3uKBQIcRr/GNCiGfjh2vdu5eDUd4BYZWjcegbr/WgDg4JIUMWND78vxYc33ybAhFI/VAoL+Y1NcAFAY9vIwObR4tg8wLby44kVAJiUI4kXFI1uJOwiWLvTMImb+rfI6lnHYc0autGm0QkGJrXA5uGpEPr88azkZDS1q7mvDpmUmu8Z3Pp2ShIvW033Ybnb0VAUpvVnAcj6Wu5/fjPX7wkO5QU/zsZFEQGXlILPaV76cVMqkf15d8jqFKspH7807Qyr87DGJ40wJ9ak5Co89CMJmkZ1OAgCju2ah4S6A2EZz/Pl3hwJK2354b0PIseOBII+2tDevQnKBQJXGryQQLP3hGTICztSPYyJclGiy/4BNL4kg/zqhUpyW4NKjuSzKiqTffltCTb843xDvUUezWKmFQswhg9Xt0vUmh4SHZUrUcSF2maHi3egY1kUCs3xEeC+6z+ByNpHFzrcMFJSr4TVlJ65EvHi+SktDxKj9u8IycdZuLaU1CTLna6yElFebB7lzfEJIh42hG9t5lS3qVHrRpOslF8yo2WeUdtHGFFRRomf6rn0tXnvBoSDCLSMlcfll5p5qbUr9qRkbrlRXb4lJJkw56+50Ky/HQvPwwJesKsRKwuAqrNCEnGQYCg56yhzE2yd+QmECTQF+R2KxIkezBzIyTZ1dUi2ZDM6m6+d+cYEqC0LDYkEEOX0JLGb0P/b0iaXyfUpOlzQ7IMIf00EgQgILUrb/SaGD2ZNzKzMU0aQ+Jve/EPXNSkfrc1E0xSENZH0noGbWLtSBr2iwXh0R3ia1ls7wn8Jj9a00ZkLJpR5h8Pr1FlXh4s6iBn1GdnqIhn1NNdP7Kwk41L3BqLDka7LOx4OQK7btS+NstfJvlrX0wricNmFfvNuGY5j6ijzJWWZNekUv7dBruxcQ2i2n9qYVfoaquGbKerud1Yp9q9uz8zT7YjeVsHoH70fi52N7x13+HHoXqPswSo9lM+5yPinMSId7oEh/I62BN6lGzKDT0O9iAOHPm2R7TTWrcniC+Cv7duB8SpundQO3T+oIq7bxE68j2IFn5wLdopVc+CFtwpc48uxlaJOBe02GdCxBrHR6WocOg9TrIjcwIXSdGUhziEEKpKstthRVcdd9PdlFVQl6eRaSu7olXw3FclEZbFnaAYqgrT45DQkFePnTw9T/VnelrlRwya06UHLqkJCSGBiZr/sFIlZzV1tKrZlddGTRK8QghzyfHSaE/mMuqVEPir0Get81fWR/y1gAkVATSfs4xhTZyfj0gi/vqGmJiYmJiYmJiYmJiYmJjYf2l/ARZIBl5THy27AAAAAElFTkSuQmCC"
                    }
                )

    if prometheus_enabled:
        openunison_helm_values["openunison"]["apps"].append(
            {
                "name": "prometheus",
                "label": "Prometheus",
                "org": "b1bf4c92-7220-4ad2-91af-ee0fe0af7312",
                "badgeUrl": "https://prometheus." + domain_suffix + "/",
                "injectToken": False,
                "proxyTo": "http://prometheus.monitoring.svc:9090${fullURI}",
                "az_groups": az_groups,
                "icon": "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"
            }
        )

        openunison_helm_values["openunison"]["apps"].append(
                    {
                        "name": "alertmanager",
                        "label": "Alert Manager",
                        "org": "b1bf4c92-7220-4ad2-91af-ee0fe0af7312",
                        "badgeUrl": "https://alertmanager." + domain_suffix + "/",
                        "injectToken": False,
                        "proxyTo": "http://alertmanager.monitoring.svc:9093${fullURI}",
                        "az_groups": az_groups,
                        "icon": "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"
                    }
        )

        openunison_helm_values["openunison"]["apps"].append(
                            {
                                "name": "grafana",
                                "label": "Grafana",
                                "org": "b1bf4c92-7220-4ad2-91af-ee0fe0af7312",
                                "badgeUrl": "https://grafana." + domain_suffix + "/",
                                "injectToken": False,
                                "azSuccessResponse":"grafana",
                                "proxyTo": "http://grafana.monitoring.svc${fullURI}",
                                "az_groups": az_groups,
                                "icon": "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Pk4fgCx1lb4BDg6B0RJlr/u8O9Ld279n2v39AC/icoySQsigAAANHGlUWHRYTUw6Y29tLmFkb2JlLnhtcAAAAAAAPD94cGFja2V0IGJlZ2luPSLvu78iIGlkPSJXNU0wTXBDZWhpSHpyZVN6TlRjemtjOWQiPz4KPHg6eG1wbWV0YSB4bWxuczp4PSJhZG9iZTpuczptZXRhLyIgeDp4bXB0az0iWE1QIENvcmUgNC40LjAtRXhpdjIiPgogPHJkZjpSREYgeG1sbnM6cmRmPSJodHRwOi8vd3d3LnczLm9yZy8xOTk5LzAyLzIyLXJkZi1zeW50YXgtbnMjIj4KICA8cmRmOkRlc2NyaXB0aW9uIHJkZjphYm91dD0iIgogICAgeG1sbnM6eG1wTU09Imh0dHA6Ly9ucy5hZG9iZS5jb20veGFwLzEuMC9tbS8iCiAgICB4bWxuczpzdEV2dD0iaHR0cDovL25zLmFkb2JlLmNvbS94YXAvMS4wL3NUeXBlL1Jlc291cmNlRXZlbnQjIgogICAgeG1sbnM6ZGM9Imh0dHA6Ly9wdXJsLm9yZy9kYy9lbGVtZW50cy8xLjEvIgogICAgeG1sbnM6R0lNUD0iaHR0cDovL3d3dy5naW1wLm9yZy94bXAvIgogICAgeG1sbnM6dGlmZj0iaHR0cDovL25zLmFkb2JlLmNvbS90aWZmLzEuMC8iCiAgICB4bWxuczp4bXA9Imh0dHA6Ly9ucy5hZG9iZS5jb20veGFwLzEuMC8iCiAgIHhtcE1NOkRvY3VtZW50SUQ9ImdpbXA6ZG9jaWQ6Z2ltcDo3OTljYmNiOC04NzFiLTRhOTAtOTliMy1jNWEyNjQzMTMxZmUiCiAgIHhtcE1NOkluc3RhbmNlSUQ9InhtcC5paWQ6MDEzNGU2ZDEtYzkyZC00MzlhLTgyODEtODUzNjcyNmZjNGYwIgogICB4bXBNTTpPcmlnaW5hbERvY3VtZW50SUQ9InhtcC5kaWQ6YTg2OWVkYWUtZjY1MC00NGZlLThhMzItOTkyNGMyMDI1NGZjIgogICBkYzpGb3JtYXQ9ImltYWdlL3BuZyIKICAgR0lNUDpBUEk9IjIuMCIKICAgR0lNUDpQbGF0Zm9ybT0iTWFjIE9TIgogICBHSU1QOlRpbWVTdGFtcD0iMTY4NDM1NTI0MzkwMTExNSIKICAgR0lNUDpWZXJzaW9uPSIyLjEwLjI4IgogICB0aWZmOk9yaWVudGF0aW9uPSIxIgogICB4bXA6Q3JlYXRvclRvb2w9IkdJTVAgMi4xMCI+CiAgIDx4bXBNTTpIaXN0b3J5PgogICAgPHJkZjpTZXE+CiAgICAgPHJkZjpsaQogICAgICBzdEV2dDphY3Rpb249InNhdmVkIgogICAgICBzdEV2dDpjaGFuZ2VkPSIvIgogICAgICBzdEV2dDppbnN0YW5jZUlEPSJ4bXAuaWlkOjgxZTgzYmI5LTYxYjgtNDY1MS04ZDkwLWUwY2MzNzY1YWY3YyIKICAgICAgc3RFdnQ6c29mdHdhcmVBZ2VudD0iR2ltcCAyLjEwIChNYWMgT1MpIgogICAgICBzdEV2dDp3aGVuPSIyMDIzLTA1LTE3VDE2OjI3OjIzLTA0OjAwIi8+CiAgICA8L3JkZjpTZXE+CiAgIDwveG1wTU06SGlzdG9yeT4KICA8L3JkZjpEZXNjcmlwdGlvbj4KIDwvcmRmOlJERj4KPC94OnhtcG1ldGE+CiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgCiAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAKICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgIAogICAgICAgICAgICAgICAgICAgICAgICAgICAKPD94cGFja2V0IGVuZD0idyI/PhgsBSQAAAAGYktHRAD/AP8A/6C9p5MAAAAJcEhZcwAACxMAAAsTAQCanBgAAAAHdElNRQfnBREUGxfWGnvhAAAgAElEQVR42u2dd3hc1dHGf3N3V83SSi64V9wkY9MhGAzGTgj1o8ZgS6EYcMEYQygOJZQQmqk2PQaMCbaMgRBCTYCA+SghGAIOYMnGDTDuTatqSXvn++MuhI9iS7J2b9nzPs95RJF2554z7505c+bMgIGBgYGBgYGBgYGBgYGBgYGBgYGBgYGB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"
                            }
                        )

    orchesrta_login_portal_helm_values = json.loads(json.dumps(openunison_helm_values))
    openunison_helm_values["dashboard"]["service_name"] = db_release.name.apply(lambda name: name)
    openunison_helm_values["dashboard"]["cert_name"] = db_release.name.apply(lambda name: name + "-certs")
    orchesrta_login_portal_helm_values["dashboard"]["service_name"] = db_release.name.apply(lambda name: name)

    # Fetch the latest version from the helm chart index
    chart_name = "openunison-operator"
    chart_index_path = "index.yaml"
    chart_url = "https://nexus.tremolo.io/repository/helm"
    index_url = f"{chart_url}/{chart_index_path}"
    chart_version = get_latest_helm_chart_version(index_url,chart_name)

    openunison_operator_release = k8s.helm.v3.Release(
        'openunison-operator',
        k8s.helm.v3.ReleaseArgs(
            chart=chart_name,
            version=chart_version,
            values=openunison_helm_values,
            namespace='openunison',
            skip_await=False,
            repository_opts= k8s.helm.v3.RepositoryOptsArgs(
                repo=chart_url
            ),
        ),
        opts=pulumi.ResourceOptions(
            provider = k8s_provider,
            depends_on=[openunison_certificate],
            custom_timeouts=pulumi.CustomTimeouts(
                create="8m",
                update="10m",
                delete="10m"
            )
        )
    )

    raw_secret_data = {
        "K8S_DB_SECRET": secrets.token_urlsafe(64),
        "unisonKeystorePassword": secrets.token_urlsafe(64),

    }
    encoded_secret_data = {
        key: base64.b64encode(value.encode('utf-8')).decode('utf-8')
            for key, value in raw_secret_data.items()
    }

    orchestra_secret_source = k8s.core.v1.Secret(
        "orchestra-secrets-source",
        metadata= k8s.meta.v1.ObjectMetaArgs(
            name="orchestra-secrets-source",
            namespace="openunison"
        ),
        data={
            "K8S_DB_SECRET": encoded_secret_data['K8S_DB_SECRET'],
            "unisonKeystorePassword": encoded_secret_data["unisonKeystorePassword"],
            "GITHUB_SECRET_ID": config.require_secret('openunison.github.client_secret').apply(lambda client_secret : base64.b64encode(client_secret.encode('utf-8')).decode('utf-8') ),
        },
        opts=pulumi.ResourceOptions(
            provider = k8s_provider,
            retain_on_delete=False,
            delete_before_replace=True,
            custom_timeouts=pulumi.CustomTimeouts(
                create="10m",
                update="10m",
                delete="10m"
            )
        )
    )

    orchestra_chart_name = 'orchestra'
    orchestra_chart_version = get_latest_helm_chart_version(index_url,orchestra_chart_name)
    openunison_orchestra_release = k8s.helm.v3.Release(
        'orchestra',
        k8s.helm.v3.ReleaseArgs(
            chart=orchestra_chart_name,
            version=orchestra_chart_version,
            values=openunison_helm_values,
            namespace='openunison',
            skip_await=False,
            wait_for_jobs=True,
            repository_opts= k8s.helm.v3.RepositoryOptsArgs(
                repo=chart_url
            ),

        ),

        opts=pulumi.ResourceOptions(
            provider = k8s_provider,
            depends_on=[openunison_operator_release,orchestra_secret_source],
            custom_timeouts=pulumi.CustomTimeouts(
                create="8m",
                update="10m",
                delete="10m"
            )
        )
    )

    pulumi.export("openunison_orchestra_release",openunison_orchestra_release)

    orchesrta_login_portal_helm_values["impersonation"]["orchestra_release_name"] = openunison_orchestra_release.name.apply(lambda name: name)

    orchestra_login_portal_chart_name = 'orchestra-login-portal'
    orchestra_login_portal_chart_version = get_latest_helm_chart_version(index_url,orchestra_login_portal_chart_name)
    openunison_orchestra_login_portal_release = k8s.helm.v3.Release(
        'orchestra-login-portal',
        k8s.helm.v3.ReleaseArgs(
            chart=orchestra_login_portal_chart_name,
            version=orchestra_login_portal_chart_version,
            values=orchesrta_login_portal_helm_values,
            namespace='openunison',
            skip_await=False,
            wait_for_jobs=True,
            repository_opts= k8s.helm.v3.RepositoryOptsArgs(
                repo=chart_url
            ),

        ),

        opts=pulumi.ResourceOptions(
            provider = k8s_provider,
            depends_on=[openunison_orchestra_release],
            custom_timeouts=pulumi.CustomTimeouts(
                create="8m",
                update="10m",
                delete="10m"
            )
        )
    )

    orchestra_kube_oidc_proxy_chart_name = 'orchestra-kube-oidc-proxy'
    orchestra_kube_oidc_proxy_chart_version = get_latest_helm_chart_version(index_url,orchestra_kube_oidc_proxy_chart_name)
    openunison_kube_oidc_proxy_release = k8s.helm.v3.Release(
        'orchestra-kube-oidc-proxy',
        k8s.helm.v3.ReleaseArgs(
            chart=orchestra_kube_oidc_proxy_chart_name,
            version=orchestra_kube_oidc_proxy_chart_version,
            values=orchesrta_login_portal_helm_values,
            namespace='openunison',
            skip_await=False,
            wait_for_jobs=True,
            repository_opts= k8s.helm.v3.RepositoryOptsArgs(
                repo=chart_url
            ),

        ),

        opts=pulumi.ResourceOptions(
            provider = k8s_provider,
            depends_on=[openunison_orchestra_login_portal_release],
            custom_timeouts=pulumi.CustomTimeouts(
                create="8m",
                update="10m",
                delete="10m"
            )
        )
    )



    cluster_admin_cluster_role_binding = k8s.rbac.v1.ClusterRoleBinding(
        "clusteradmin-clusterrolebinding",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="openunison-github-cluster-admins"
        ),
        role_ref=k8s.rbac.v1.RoleRefArgs(
            api_group="rbac.authorization.k8s.io",  # The API group of the role being referenced
            kind="ClusterRole",  # Indicates the kind of role being referenced
            name="cluster-admin"  # The name of the ClusterRole you're binding
        ),
        subjects=subjects,
        opts=pulumi.ResourceOptions(
            provider = k8s_provider,
            depends_on=[],
            custom_timeouts=pulumi.CustomTimeouts(
                create="8m",
                update="10m",
                delete="10m"
            )
        )
    )


    if prometheus_enabled:
        # create the Grafana ResultGroup
        openunison_grafana_resultgroup = CustomResource(
            "openunison-grafana",
            api_version="openunison.tremolo.io/v1",
            kind="ResultGroup",
            metadata={
                "labels": {
                    "app.kubernetes.io/component": "openunison-resultgroups",
                    "app.kubernetes.io/instance": "openunison-orchestra-login-portal",
                    "app.kubernetes.io/name": "openunison",
                    "app.kubernetes.io/part-of": "openunison"
                    },
                "name": "grafana",
                "namespace": "openunison"
            },
            spec=[
                {
                "resultType": "header",
                "source": "static",
                "value": "X-WEBAUTH-GROUPS=Admin"
                },
                {
                "resultType": "header",
                "source": "user",
                "value": "X-WEBAUTH-USER=uid"
                }
            ],
            opts=pulumi.ResourceOptions(
                provider = k8s_provider,
                depends_on=[openunison_orchestra_release],
                custom_timeouts=pulumi.CustomTimeouts(
                    create="5m",
                    update="10m",
                    delete="10m"
                )
            )
        )

def deploy_kubernetes_dashboard(name: str, k8s_provider: Provider, kubernetes_distribution: str, project_name: str, namespace: str):
    # Deploy kubernetes-dashboard via the helm chart
    # Create a Namespace
    dashboard_namespace = k8s.core.v1.Namespace("kubernetes-dashboard",
        metadata= k8s.meta.v1.ObjectMetaArgs(
            name="kubernetes-dashboard"
        ),
        opts=pulumi.ResourceOptions(
            provider = k8s_provider,
            retain_on_delete=True,
            custom_timeouts=pulumi.CustomTimeouts(
                create="10m",
                update="10m",
                delete="10m"
            )
        )
    )

    # Fetch the latest version from the helm chart index
    chart_name = "kubernetes-dashboard"
    chart_index_path = "index.yaml"
    chart_url = "https://kubernetes.github.io/dashboard"
    index_url = f"{chart_url}/{chart_index_path}"
    chart_version = "6.0.8"

    k8s_db_release = k8s.helm.v3.Release(
            'kubernetes-dashboard',
            k8s.helm.v3.ReleaseArgs(
                chart=chart_name,
                version=chart_version,
                namespace='kubernetes-dashboard',
                skip_await=False,
                repository_opts= k8s.helm.v3.RepositoryOptsArgs(
                    repo=chart_url
                ),
            ),
            opts=pulumi.ResourceOptions(
                provider = k8s_provider,
                depends_on=[dashboard_namespace],
                custom_timeouts=pulumi.CustomTimeouts(
                    create="8m",
                    update="10m",
                    delete="10m"
                )
            )
        )

    return k8s_db_release
