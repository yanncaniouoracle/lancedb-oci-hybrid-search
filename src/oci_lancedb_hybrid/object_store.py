"""Object Storage readers for OCI instance principals and S3 customer secrets."""

from __future__ import annotations

import os
from typing import Protocol
from urllib.parse import urlparse

import boto3
from botocore.client import Config


def split_s3_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
        raise ValueError(f"Expected s3://bucket/key, got {uri!r}")
    return parsed.netloc, parsed.path.lstrip("/")


class ObjectReader(Protocol):
    def get(self, uri: str) -> bytes: ...


class InstancePrincipalReader:
    """Native OCI Object Storage access; requires dynamic-group IAM policy."""

    def __init__(self) -> None:
        import oci

        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        self.client = oci.object_storage.ObjectStorageClient(config={}, signer=signer)
        self.namespace = os.environ.get("OCI_NAMESPACE") or self.client.get_namespace().data

    def get(self, uri: str) -> bytes:
        bucket, key = split_s3_uri(uri)
        return self.client.get_object(self.namespace, bucket, key).data.content


def s3_client_from_environment():
    """Create an OCI S3-compatible client for an explicitly authorized writer."""
    namespace = os.environ["OCI_NAMESPACE"]
    region = os.environ["OCI_REGION"]
    endpoint = os.environ.get(
        "OCI_S3_ENDPOINT",
        f"https://{namespace}.compat.objectstorage.{region}.oraclecloud.com",
    )
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=os.environ["OCI_S3_ACCESS_KEY"],
        aws_secret_access_key=os.environ["OCI_S3_SECRET_KEY"],
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )


class S3CustomerSecretReader(ObjectReader):
    """OCI S3-compatible access for a customer secret key, normally read-only."""

    def __init__(self) -> None:
        self.client = s3_client_from_environment()

    def get(self, uri: str) -> bytes:
        bucket, key = split_s3_uri(uri)
        return self.client.get_object(Bucket=bucket, Key=key)["Body"].read()


def reader_from_environment() -> ObjectReader:
    if os.environ.get("OCI_AUTH") == "instance_principal":
        return InstancePrincipalReader()
    return S3CustomerSecretReader()
