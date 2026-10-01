data "oci_objectstorage_namespace" "current" {
  compartment_id = var.compartment_ocid
}

data "oci_core_subnet" "search" {
  subnet_id = var.subnet_ocid
}

data "oci_core_subnet" "gpu_clients" {
  subnet_id = var.gpu_subnet_id
}

data "oci_core_vcn" "search" {
  vcn_id = var.vcn_id
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

  # The raw-payload bucket is assumed to be in this tenancy.
  namespace = data.oci_objectstorage_namespace.current.namespace
  network_compartment_ocid = coalesce(var.network_compartment_ocid, var.compartment_ocid)
  tags                     = merge(var.common_freeform_tags, { "lancedb-search-service" = var.deployment_name })
  gpu_client_cidrs = toset([data.oci_core_subnet.gpu_clients.cidr_block])
}

resource "oci_core_network_security_group" "search" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.deployment_name}-search"
  freeform_tags  = local.tags
  vcn_id         = var.vcn_id

  lifecycle {
    precondition {
      condition     = data.oci_core_subnet.search.vcn_id == var.vcn_id
      error_message = "subnet_ocid must belong to vcn_id."
    }
    precondition {
      condition     = data.oci_core_subnet.gpu_clients.vcn_id == var.vcn_id
      error_message = "gpu_subnet_id must belong to vcn_id."
    }
    precondition {
      condition     = data.oci_core_vcn.search.compartment_id == local.network_compartment_ocid
      error_message = "vcn_id must belong to network_compartment_ocid."
    }
  }
}

resource "oci_core_network_security_group" "load_balancer" {
  count          = var.enable_private_load_balancer ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = "${var.deployment_name}-lb"
  freeform_tags  = local.tags
  vcn_id         = var.vcn_id
}

resource "oci_core_network_security_group_security_rule" "search_egress" {
  network_security_group_id = oci_core_network_security_group.search.id
  direction                 = "EGRESS"
  protocol                  = "all"
  destination               = "0.0.0.0/0"
  destination_type          = "CIDR_BLOCK"
}

resource "oci_core_network_security_group_security_rule" "direct_client_ingress" {
  for_each                  = var.enable_private_load_balancer ? toset([]) : local.gpu_client_cidrs
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

# OCI Load Balancer health probes originate from managed service addresses in
# the LB subnet rather than from the listener VIP.  Allow that subnet to reach
# the backend health/API port.  Use a dedicated LB subnet in production to
# keep this CIDR as narrow as possible.
resource "oci_core_network_security_group_security_rule" "load_balancer_health_check_to_search" {
  count                     = var.enable_private_load_balancer ? 1 : 0
  network_security_group_id = oci_core_network_security_group.search.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = data.oci_core_subnet.search.cidr_block
  source_type               = "CIDR_BLOCK"
  tcp_options {
    destination_port_range {
      min = var.service_port
      max = var.service_port
    }
  }
}

resource "oci_core_network_security_group_security_rule" "client_to_load_balancer" {
  for_each                  = var.enable_private_load_balancer ? local.gpu_client_cidrs : toset([])
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

resource "oci_core_network_security_group_security_rule" "controller_to_ingestion_workers" {
  count                     = var.enable_object_event_ingestion ? 1 : 0
  network_security_group_id = oci_core_network_security_group.search.id
  direction                 = "INGRESS"
  protocol                  = "6"
  source                    = oci_core_network_security_group.search.id
  source_type               = "NETWORK_SECURITY_GROUP"
  tcp_options {
    destination_port_range {
      min = var.ingestion_worker_port
      max = var.ingestion_worker_port
    }
  }
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
  object_events_enabled = var.enable_object_event_ingestion
  freeform_tags  = local.tags

  # Prevent raw-payload deletion when an existing stack is reconfigured.
  lifecycle {
    prevent_destroy = true
  }
}

resource "oci_queue_queue" "object_events" {
  count                = var.enable_object_event_ingestion ? 1 : 0
  compartment_id       = var.compartment_ocid
  display_name         = "${var.deployment_name}-object-events"
  retention_in_seconds = var.ingestion_queue_retention_seconds
  visibility_in_seconds = var.ingestion_queue_visibility_seconds
  timeout_in_seconds   = 30
  freeform_tags        = local.tags
}

resource "oci_functions_application" "object_event_router" {
  count                      = var.enable_object_event_ingestion ? 1 : 0
  compartment_id             = var.compartment_ocid
  display_name               = "${var.deployment_name}-object-event-router"
  subnet_ids                 = [var.subnet_ocid]
  network_security_group_ids = [oci_core_network_security_group.search.id]
  freeform_tags              = local.tags
}

resource "oci_functions_function" "object_event_router" {
  count          = var.enable_object_event_ingestion ? 1 : 0
  application_id = oci_functions_application.object_event_router[0].id
  display_name   = "${var.deployment_name}-object-event-router"

  source_details {
    image       = var.ingestion_function_image
    source_type = "CONTAINER_IMAGE"
  }
  memory_in_mbs  = var.ingestion_function_memory_mbs
  timeout_in_seconds = 30
  config = {
    QUEUE_ENDPOINT = oci_queue_queue.object_events[0].messages_endpoint
    QUEUE_ID       = oci_queue_queue.object_events[0].id
  }
  freeform_tags = local.tags

  lifecycle {
    precondition {
      condition     = var.ingestion_function_image != null && trimspace(var.ingestion_function_image) != ""
      error_message = "ingestion_function_image must reference the pre-built function/object_event_router OCIR image when enable_object_event_ingestion is true."
    }
    precondition {
      condition     = !var.enable_object_event_ingestion || length(jsondecode(var.ingestion_source_routes_json)) > 0
      error_message = "ingestion_source_routes_json must contain at least one bucket/prefix/table route when object-event ingestion is enabled."
    }
  }
}

resource "oci_events_rule" "object_changes" {
  count          = var.enable_object_event_ingestion ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = "${var.deployment_name}-object-changes"
  description    = "Region-wide Object Storage create, update, and delete events for LanceDB ingestion."
  is_enabled     = true
  freeform_tags  = local.tags

  condition_details {
    event_types = [
      "com.oraclecloud.objectstorage.createobject",
      "com.oraclecloud.objectstorage.updateobject",
      "com.oraclecloud.objectstorage.deleteobject",
    ]
    data = jsonencode({})
  }

  actions {
    action {
      action_type = "FAAS"
      is_enabled  = true
      function_id = oci_functions_function.object_event_router[0].id
      description = "Queue Object Storage changes for the LanceDB ingestion controller."
    }
  }
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

resource "oci_identity_policy" "search_queue_consume" {
  count          = var.create_instance_principal_policy && var.enable_object_event_ingestion ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.deployment_name}-queue-consume"
  description    = "Allow the first LanceDB search node to consume Object Storage update events."
  statements = [
    "Allow dynamic-group ${var.dynamic_group_name} to use queue-pull in compartment id ${var.compartment_ocid} where target.queue.id = '${oci_queue_queue.object_events[0].id}'",
  ]
}

resource "oci_identity_policy" "function_queue_publish" {
  count          = var.create_function_queue_policy && var.enable_object_event_ingestion ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.deployment_name}-function-queue-publish"
  description    = "Allow only Functions resource principals in this compartment to publish Object Storage events to the ingestion queue."
  statements = [
    "Allow any-user to use queue-push in compartment id ${var.compartment_ocid} where all {request.principal.type = 'fnfunc', target.queue.id = '${oci_queue_queue.object_events[0].id}'}",
  ]
}

resource "oci_load_balancer_load_balancer" "search" {
  count          = var.enable_private_load_balancer ? 1 : 0
  compartment_id = var.compartment_ocid
  display_name   = "${var.deployment_name}-private"
  is_private     = true
  subnet_ids     = [var.subnet_ocid]
  shape          = "flexible"
  freeform_tags  = local.tags
  # The search-node ingress rule authorizes this NSG.  It must be attached to
  # the load balancer so its health checks and forwarded connections carry the
  # expected source identity.
  network_security_group_ids = [oci_core_network_security_group.load_balancer[0].id]

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
