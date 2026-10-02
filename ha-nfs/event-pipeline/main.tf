data "oci_objectstorage_namespace" "current" {
  compartment_id = var.compartment_ocid
}

locals {
  tags = merge(var.common_freeform_tags, { "lancedb-ha-nfs" = var.deployment_name })
}

resource "oci_queue_queue" "object_events" {
  compartment_id        = var.compartment_ocid
  display_name          = "${var.deployment_name}-object-events"
  retention_in_seconds  = var.queue_retention_seconds
  visibility_in_seconds = var.queue_visibility_seconds
  timeout_in_seconds    = 30
  freeform_tags         = local.tags
}

resource "oci_functions_application" "router" {
  compartment_id             = var.compartment_ocid
  display_name               = "${var.deployment_name}-object-event-router"
  subnet_ids                 = [var.functions_subnet_ocid]
  network_security_group_ids = var.function_nsg_ids
  freeform_tags              = local.tags
}

resource "oci_functions_function" "router" {
  application_id      = oci_functions_application.router.id
  display_name        = "${var.deployment_name}-object-event-router"
  memory_in_mbs       = var.function_memory_mbs
  timeout_in_seconds  = 30
  freeform_tags       = local.tags

  source_details {
    image       = var.ingestion_function_image
    source_type = "CONTAINER_IMAGE"
  }

  config = {
    QUEUE_ENDPOINT = oci_queue_queue.object_events.messages_endpoint
    QUEUE_ID       = oci_queue_queue.object_events.id
  }
}

resource "oci_events_rule" "object_changes" {
  compartment_id = var.compartment_ocid
  display_name   = "${var.deployment_name}-object-changes"
  description    = "Region-wide Object Storage events for the HA-NFS LanceDB ingestion pipeline."
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
      function_id = oci_functions_function.router.id
      description = "Normalize Object Storage events and publish them to the HA-NFS Queue."
    }
  }
}

resource "oci_identity_policy" "function_queue_publish" {
  count          = var.create_function_queue_policy ? 1 : 0
  compartment_id = var.compartment_ocid
  name           = "${var.deployment_name}-function-queue-publish"
  description    = "Allow the HA-NFS event router Function to publish only to its Queue."
  statements = [
    "Allow any-user to use queue-push in compartment id ${var.compartment_ocid} where all {request.principal.type = 'fnfunc', target.queue.id = '${oci_queue_queue.object_events.id}'}",
  ]
}
