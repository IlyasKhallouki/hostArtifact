import copy

from google.cloud import firestore

from publisher.models import empty_state


class FirestoreMetadataStore:
    """Small-volume serializable metadata transactions; HTML is never in Firestore."""

    def __init__(self, project: str, database: str):
        self.client = firestore.Client(project=project, database=database)

    def _read(self, transaction=None):
        state = empty_state()
        for name in state:
            if name == "audit":
                continue  # Append-only; no need to load historical audit data.
            for doc in self.client.collection(name).stream(transaction=transaction):
                state[name][doc.id] = doc.to_dict()
        return state

    def read(self):
        return self._read()

    def transaction(self, change):
        @firestore.transactional
        def run(transaction):
            # A shared lock document makes scans serializable even for new documents.
            lock = self.client.collection("internal").document("metadata_lock")
            lock.get(transaction=transaction)
            before = self._read(transaction)
            state = copy.deepcopy(before)
            result = change(state)
            for name, items in state.items():
                for key in before[name].keys() - items.keys():
                    transaction.delete(self.client.collection(name).document(key))
                for key, value in items.items():
                    if before[name].get(key) != value:
                        transaction.set(
                            self.client.collection(name).document(key), value
                        )
            transaction.set(lock, {"updated_at": firestore.SERVER_TIMESTAMP})
            return result

        return run(self.client.transaction())
