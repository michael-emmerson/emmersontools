"""Cloud mode, used when the app runs in a Cloudflare Container.

A container's disk is wiped whenever it stops, so the spreadsheet, settings.json and the backups are
mirrored to an R2 bucket. The container can't reach R2 directly: it sends plain HTTP requests to
RRR_STORAGE_URL (http://r2.internal), which the Worker intercepts and answers from the bucket.

    GET  /<key>   -> the file, or 404
    PUT  /<key>   -> stores the request body

On start the app restores the spreadsheet and settings.json from the bucket. After every change it
uploads whatever changed (and any new backup) before it answers, so a change the person sees as saved
is in R2.
"""
import os
import threading
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 30


class StorageError(Exception):
    pass


class Mirror:
    def __init__(self, base_url, files, backups_dir, backups_prefix="backups/"):
        """`files` maps a bucket key to a local path. Files in `backups_dir` go under `backups_prefix`."""
        self.base_url = base_url.rstrip("/")
        self.files = dict(files)
        self.backups_dir = backups_dir
        self.backups_prefix = backups_prefix
        self._seen = {}  # local path -> (mtime_ns, size) as last restored or uploaded
        self._lock = threading.Lock()

    # Bucket access -------------------------------------------------------------------------------------
    def _url(self, key):
        return f"{self.base_url}/{urllib.parse.quote(key)}"

    def get(self, key):
        try:
            with urllib.request.urlopen(self._url(key), timeout=TIMEOUT) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise StorageError(f"Couldn't read {key} from Cloudflare storage (HTTP {exc.code}).") from exc
        except OSError as exc:
            raise StorageError(f"Couldn't reach Cloudflare storage to read {key}: {exc}") from exc

    def put(self, key, data):
        req = urllib.request.Request(self._url(key), data=data, method="PUT",
                                     headers={"Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            raise StorageError(f"Couldn't save {key} to Cloudflare storage (HTTP {exc.code}).") from exc
        except OSError as exc:
            raise StorageError(f"Couldn't reach Cloudflare storage to save {key}: {exc}") from exc

    # Local state ---------------------------------------------------------------------------------------
    @staticmethod
    def _stamp(path):
        try:
            st = os.stat(path)
        except FileNotFoundError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def restore(self):
        """Downloads the mirrored files. Returns the keys that were found in the bucket."""
        found = []
        with self._lock:
            for key, path in self.files.items():
                data = self.get(key)
                if data is None:
                    continue
                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                tmp = path + ".restore"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, path)
                self._seen[path] = self._stamp(path)
                found.append(key)
            # Backups already in the bucket don't need uploading again; only new ones do.
            if os.path.isdir(self.backups_dir):
                for name in os.listdir(self.backups_dir):
                    path = os.path.join(self.backups_dir, name)
                    self._seen[path] = self._stamp(path)
        return found

    def sync(self):
        """Uploads every mirrored file and backup that changed since it was last restored or uploaded.
        Returns the keys uploaded. Raises StorageError if any upload fails (the rest are still tried)."""
        uploaded, errors = [], []
        with self._lock:
            pending = list(self.files.items())
            if os.path.isdir(self.backups_dir):
                # Backups first, so the bucket never has a new spreadsheet without the backup of the old one.
                backups = sorted(os.listdir(self.backups_dir))
                pending = [(self.backups_prefix + n, os.path.join(self.backups_dir, n)) for n in backups] + pending
            for key, path in pending:
                stamp = self._stamp(path)
                if stamp is None or self._seen.get(path) == stamp:
                    continue
                try:
                    with open(path, "rb") as fh:
                        self.put(key, fh.read())
                except (StorageError, OSError) as exc:
                    errors.append(str(exc))
                    continue
                self._seen[path] = stamp
                uploaded.append(key)
        if errors:
            raise StorageError(" ".join(errors))
        return uploaded
