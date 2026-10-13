from google.api_core.exceptions import PreconditionFailed
from google.cloud import storage


class GCSBlobStore:
    def __init__(self, project, bucket):
        self.bucket = storage.Client(project=project).bucket(bucket)

    def put(self, path, html):
        blob = self.bucket.blob(path)
        try:
            blob.upload_from_string(
                html, content_type="text/html; charset=utf-8", if_generation_match=0
            )
        except PreconditionFailed:
            if blob.download_as_text() != html:
                raise ValueError("Immutable blob already exists") from None

    def get(self, path):
        return self.bucket.blob(path).download_as_text()

    def delete_prefix(self, prefix):
        for blob in self.bucket.list_blobs(prefix=prefix):
            blob.delete(if_generation_match=blob.generation)
