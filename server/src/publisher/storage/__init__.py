def create_stores(settings):
    if settings.storage_backend == "local":
        from publisher.storage.local_store import LocalBlobStore, LocalMetadataStore

        return LocalMetadataStore(settings.local_data), LocalBlobStore(
            settings.local_data
        )
    from publisher.storage.firestore_store import FirestoreMetadataStore
    from publisher.storage.gcs_store import GCSBlobStore

    return FirestoreMetadataStore(
        settings.gcp_project_id, settings.firestore_database
    ), GCSBlobStore(settings.gcp_project_id, settings.bucket)
