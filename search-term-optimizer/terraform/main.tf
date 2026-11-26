# Déploiement Cloud Run, adapté du Terraform de Negative Keyword Cleaner
# (google-marketing-solutions, licence Apache 2.0).

resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "iam.googleapis.com",
    "googleads.googleapis.com",
    "generativelanguage.googleapis.com",
    "storage.googleapis.com",
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "main" {
  account_id   = "search-term-optimizer"
  display_name = "Search Term Optimizer"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "logs_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.main.email}"
}

resource "random_id" "bucket_suffix" {
  byte_length = 6
}

# Mémoire par client (contexte, seuils, marques, décisions validées).
# force_destroy = false : un `terraform destroy` ne supprime pas la mémoire par erreur.
resource "google_storage_bucket" "main" {
  name                        = "search-term-optimizer-${random_id.bucket_suffix.hex}"
  location                    = var.location
  uniform_bucket_level_access = true
  force_destroy               = false
  versioning {
    enabled = true
  }
  depends_on = [google_project_service.apis]
}

resource "google_storage_bucket_iam_member" "main" {
  bucket = google_storage_bucket.main.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.main.email}"
}

resource "google_cloud_run_v2_service" "default" {
  name     = "search-term-optimizer"
  location = var.location
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account  = google_service_account.main.email
    timeout          = "1800s"
    session_affinity = true

    containers {
      image = var.image

      env {
        name  = "OAUTH_CLIENT_ID"
        value = var.google_oauth_client_id
      }
      env {
        name  = "OAUTH_CLIENT_SECRET"
        value = var.google_oauth_client_secret
      }
      env {
        name  = "MCC_ID"
        value = var.mcc_id
      }
      env {
        name  = "GOOGLE_ADS_API_TOKEN"
        value = var.google_ads_api_token
      }
      env {
        name  = "GOOGLE_API_KEY"
        value = var.gemini_api_key
      }
      env {
        name  = "GEMINI_MODEL"
        value = var.gemini_model
      }
      env {
        name  = "DEFAULT_BUCKET_NAME"
        value = google_storage_bucket.main.name
      }

      resources {
        limits = {
          cpu    = "1"
          memory = "2Gi"
        }
      }
    }
  }

  # OAUTH_REDIRECT_URI est ajouté après création (voir null_resource ci-dessous).
  lifecycle {
    ignore_changes = [template[0].containers[0].env]
  }
}

# Comme l'outil d'origine : l'URL est publique, mais rien n'est accessible sans
# connexion Google donnant accès au MCC. Remplace "allUsers" pour restreindre.
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = var.location
  name     = google_cloud_run_v2_service.default.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "null_resource" "redirect_uri" {
  triggers = {
    uri = google_cloud_run_v2_service.default.uri
  }
  provisioner "local-exec" {
    command = "gcloud run services update ${google_cloud_run_v2_service.default.name} --update-env-vars=OAUTH_REDIRECT_URI=${google_cloud_run_v2_service.default.uri} --region=${var.location} --project=${var.project_id}"
  }
}
