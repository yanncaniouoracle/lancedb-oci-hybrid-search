terraform {
  required_version = ">= 1.5.0, < 1.6.0"

  required_providers {
    oci = {
      source  = "oracle/oci"
      # Provider 8.23.0 is used by the integrated HA-NFS ORM stack.  Pinning
      # avoids the incompatible Functions image schema introduced in 9.x.
      version = "= 8.23.0"
    }
  }
}

provider "oci" {
  region = var.region
}
