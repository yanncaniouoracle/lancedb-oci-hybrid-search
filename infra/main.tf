data "oci_objectstorage_namespace" "current" {
  compartment_id = var.compartment_ocid
}

data "oci_core_subnet" "search" {
  subnet_id = var.subnet_ocid
}

data "oci_core_vcn" "search" {
  vcn_id = var.vcn_ocid
}

locals {
  search_nodes = {
    for index in range(var.search_service_count) : tostring(index) => index
  }

  volume_attachments = {
    for pair in setproduct(range(var.search_service_count), range(var.hot_tier_volume_count)) :
    "${pair[0]}-${pair[1]}" => {
      node_index   = pair[0]
      volume_index = pair[1]
    }
  }

  namespace = coalesce(
    nullif(trimspace(coalesce(var.object_storage_namespace, "")), ""),
    data.oci_objectstorage_namespace.current.namespace,
  )
  network_compartment_ocid = coalesce(var.network_compartment_ocid, var.compartment_ocid)
  tags                     = merge(var.common_freeform_tags, { "lancedb-search-service" = var.deployment_name })
  allowed_client_cidrs = toset(compact([
    for cidr in split(",", var.allowed_client_cidrs) : trimspace(cidr)
  ]))
}

resource "oci_core_network_security_group" "search" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.deployment_name}-search"
  freeform_tags  = local.tags

  lifecycle {
    precondition {
      condition     = data.oci_core_subnet.search.vcn_id == var.vcn_ocid
      error_message = "subnet_ocid must belong to vcn_ocid."
    }
    precondition {
      condition     = data.oci_core_vcn.search.compartment_id == local.network_compartment_ocid
      error_message = "vcn_ocid must belong to network_compartment_ocid."
    }
  }
}

resource "oci_core_network_security_group" "load_balancer" {
  count          = var.enable_private_load_balancer ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = "${var.deployment_name}-lb"
  freeform_tags  = local.tags
}

resource "oci_core_network_security_group_security_rule" "search_egress" {
  network_security_group_id = oci_core_network_security_group.search.id
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
}

resource "oci_core_network_security_group_security_rule" "direct_client_ingress" {
  for_each                  = var.enable_private_load_balancer ? toset([]) : local.allowed_client_cidrs
  network_security_group_id = oci_core_network_security_group.search.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = each.value
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = var.service_port
      max = var.service_port
    }
  }
}

resource "oci_core_network_security_group_security_rule" "load_balancer_to_search" {
  count                     = var.enable_private_load_balancer ? 1 : 0
  network_security_group_id = oci_core_network_security_group.search.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = oci_core_network_security_group.load_balancer[0].id
  source_type               = "NETWORK_SECURITY_GROUP"
  tcp_options {
    destination_port_range {
      min = var.service_port
      max = var.service_port
    }
  }
}

resource "oci_core_network_security_group_security_rule" "client_to_load_balancer" {
  for_each                  = var.enable_private_load_balancer ? local.allowed_client_cidrs : toset([])
  network_security_group_id = oci_core_network_security_group.load_balancer[0].id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = each.value
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = var.service_port
      max = var.service_port
    }
  }
}

resource "oci_core_network_security_group_security_rule" "load_balancer_egress" {
  count                     = var.enable_private_load_balancer ? 1 : 0
  network_security_group_id = oci_core_network_security_group.load_balancer[0].id
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
}

resource "oci_core_instance" "search" {
  for_each            = local.search_nodes
  availability_domain = var.availability_domain
  compartment_id      = var.compartment_ocid
  display_name        = "${var.deployment_name}-${each.key}"
  shape               = var.search_shape
  freeform_tags       = local.tags

  shape_config {
    ocpus         = var.search_ocpus
    memory_in_gbs = var.search_memory_gb
  }

  create_vnic_details {
    subnet_id        = var.subnet_ocid
    assign_public_ip = false
    nsg_ids          = [oci_core_network_security_group.search.id]
  }

  source_details {
    source_type             = "image"
    source_id               = var.search_image_ocid
    boot_volume_size_in_gbs = var.boot_volume_size_gb
  }

  metadata = {
    ssh_authorized_keys = var.ssh_public_key
  }
}

resource "oci_core_volume" "hot" {
  for_each            = local.volume_attachments
  availability_domain = var.availability_domain
  compartment_id      = var.compartment_ocid
  display_name        = "${var.deployment_name}-${each.value.node_index}-hot-${each.value.volume_index}"
  size_in_gbs         = var.hot_tier_volume_size_gb
  vpus_per_gb         = var.hot_tier_vpus_per_gb
  freeform_tags       = local.tags
}

resource "oci_core_volume_attachment" "hot" {
  for_each      = local.volume_attachments
  attachment_type = "paravirtualized"
  instance_id   = oci_core_instance.search[tostring(each.value.node_index)].id
  volume_id     = oci_core_volume.hot[each.key].id
  is_shareable  = false
}

resource "oci_objectstorage_bucket" "raw" {
  count          = var.create_object_storage_bucket ? 1 : 0
  compartment_id = var.compartment_ocid
  namespace      = local.namespace
  name           = var.object_storage_bucket_name
  access_type    = "NoPublicAccess"
  storage_tier   = "Standard"
  freeform_tags  = local.tags
}

resource "oci_identity_policy" "search_object_read" {
  count          = var.create_instance_principal_policy ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.deployment_name}-object-read"
  description    = "Read-only Object Storage access for the LanceDB search-service dynamic group."
  statements = [
    "Allow dynamic-group ${var.dynamic_group_name} to read buckets in compartment id ${var.compartment_ocid}",
    "Allow dynamic-group ${var.dynamic_group_name} to read objects in compartment id ${var.compartment_ocid}",
  ]

  lifecycle {
    precondition {
      condition     = var.dynamic_group_name != null && trimspace(var.dynamic_group_name) != ""
      error_message = "dynamic_group_name is required when create_instance_principal_policy is true."
    }
  }
}

resource "oci_load_balancer_load_balancer" "search" {
  count          = var.enable_private_load_balancer ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = "${var.deployment_name}-private"
  is_private     = true
  subnet_ids     = [var.subnet_ocid]
  shape          = "flexible"
  freeform_tags  = local.tags

  shape_details {
    minimum_bandwidth_in_mbps = var.load_balancer_min_bandwidth_mbps
    maximum_bandwidth_in_mbps = var.load_balancer_max_bandwidth_mbps
  }
}

resource "oci_load_balancer_backend_set" "search" {
  count            = var.enable_private_load_balancer ? 1 : 0
  load_balancer_id = oci_load_balancer_load_balancer.search[0].id
  name             = "search"
  policy           = "LEAST_CONNECTIONS"

  health_checker {
    protocol          = "HTTP"
    port              = var.service_port
    url_path          = "/healthz"
    return_code       = 200
    retries           = 3
    timeout_in_millis = 3000
    interval_ms       = 10000
  }
}

resource "oci_load_balancer_backend" "search" {
  for_each         = var.enable_private_load_balancer ? local.search_nodes : {}
  load_balancer_id = oci_load_balancer_load_balancer.search[0].id
  backendset_name  = oci_load_balancer_backend_set.search[0].name
  ip_address       = oci_core_instance.search[each.key].private_ip
  port             = var.service_port
  backup           = false
  drain            = false
  offline          = false
  weight           = 1
}

resource "oci_load_balancer_listener" "search" {
  count            = var.enable_private_load_balancer ? 1 : 0
  load_balancer_id = oci_load_balancer_load_balancer.search[0].id
  name             = "search"
  default_backend_set_name = oci_load_balancer_backend_set.search[0].name
  port             = var.service_port
  protocol         = "HTTP"
}
