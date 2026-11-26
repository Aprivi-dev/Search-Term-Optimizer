output "app_url" {
  description = "À ajouter aux URI de redirection autorisés du client OAuth"
  value       = google_cloud_run_v2_service.default.uri
}

output "bucket" {
  value = google_storage_bucket.main.name
}
