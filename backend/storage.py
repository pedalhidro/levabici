"""
Storage abstraction do backend levabici — cópia de amora/backend/storage.py
(mesma interface StateStore; manter os dois em sincronia ao corrigir bugs).

Roteia o ESTADO MUTÁVEL (o grafo reviews.ttl) por trás de uma interface comum:

  StateStore.read_text(key)              → str | None
  StateStore.write_text(key, txt, ct=…)  → None
  ...

Dois backends:
  LocalStateStore(root_dir)   — dev / local: lê e grava no filesystem
  GCSStateStore(bucket_name)  — Cloud Run: lê e grava num bucket
                                (versionamento de objetos LIGADO = histórico
                                 de edições estilo wiki; ver backend/README.md)

Estado mutável: reviews.ttl (o grafo inteiro de empresas + avaliações).
Estado estático (servido pelo Flask a partir do container): o app inteiro.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


class StateStore:
    def read_text(self, key: str) -> str | None:
        raise NotImplementedError

    def write_text(self, key: str, text: str, content_type: str = "text/turtle") -> None:
        raise NotImplementedError

    def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def delete_prefix(self, prefix: str) -> None:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError

    def public_url(self, key: str) -> str | None:
        return None

    def list_keys(self, prefix: str) -> list[str]:
        raise NotImplementedError


class LocalStateStore(StateStore):
    """Filesystem-backed store — dev / serve local."""

    def __init__(self, root_dir: str | Path):
        self.root = Path(root_dir).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        # Bloqueia escape do root via "../" — defesa em profundidade.
        # `is_relative_to` compara componentes de path (não prefixo de string),
        # então um dir irmão tipo `<root>_evil` não passa — diferente de um
        # `startswith(str(self.root))` ingênuo.
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root):
            raise ValueError(f"key escapa do root: {key}")
        return p

    def _atomic_write(self, p: Path, write_fn) -> None:
        # Grava num temp no mesmo diretório e faz os.replace (rename atômico
        # no mesmo filesystem). Evita que um leitor concorrente — ou um crash
        # no meio da escrita — veja um catálogo TTL truncado/meia-boca.
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=".tmp-", suffix=p.suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                write_fn(f)
            os.replace(tmp, p)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def read_text(self, key):
        p = self._p(key)
        if not (p.exists() and p.is_file() and p.stat().st_size > 0):
            return None
        return p.read_text(encoding="utf-8")

    # ADIÇÃO local (não existe no storage.py do amora, que serve blobs por
    # redirect a bucket público): leitura binária pra servir uploads a
    # partir de bucket PRIVADO via Flask.
    def read_bytes(self, key):
        p = self._p(key)
        if not (p.exists() and p.is_file()):
            return None
        return p.read_bytes()

    def write_text(self, key, text, content_type="text/turtle"):
        p = self._p(key)
        self._atomic_write(p, lambda f: f.write(text.encode("utf-8")))

    def write_bytes(self, key, data, content_type=None):
        p = self._p(key)
        self._atomic_write(p, lambda f: f.write(data))

    def delete(self, key):
        p = self._p(key)
        if p.is_file():
            p.unlink()

    def delete_prefix(self, prefix):
        p = self._p(prefix)
        if p.is_dir():
            for f in p.rglob("*"):
                if f.is_file():
                    try:
                        f.unlink()
                    except OSError:
                        pass
            # Remove diretórios vazios depois (deepest first)
            for d in sorted(p.rglob("*"), key=lambda x: -len(str(x))):
                if d.is_dir():
                    try:
                        d.rmdir()
                    except OSError:
                        pass
            try:
                p.rmdir()
            except OSError:
                pass

    def exists(self, key):
        return self._p(key).exists()

    def list_keys(self, prefix):
        p = self._p(prefix)
        if not p.is_dir():
            return []
        out = []
        for f in p.rglob("*"):
            if f.is_file():
                rel = f.relative_to(self.root)
                out.append(str(rel).replace("\\", "/"))
        return out


class GCSStateStore(StateStore):
    """Google Cloud Storage-backed store — Cloud Run."""

    def __init__(self, bucket_name: str):
        # Import lazy: a lib gcs é pesada e só faz sentido no modo cloud.
        from google.cloud import storage  # type: ignore

        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket_name)
        self.bucket_name = bucket_name

    # MIME guessing — só pros conteúdos que servimos por static (raro,
    # quase tudo vira redirect para o GCS público).
    @staticmethod
    def _ct_for(key: str) -> str:
        if key.endswith(".ttl"):
            return "text/turtle; charset=utf-8"
        if key.endswith(".json"):
            return "application/json"
        if key.endswith(".jpg") or key.endswith(".jpeg"):
            return "image/jpeg"
        if key.endswith(".png"):
            return "image/png"
        if key.endswith(".heic") or key.endswith(".heif"):
            return "image/heic"
        return "application/octet-stream"

    def read_text(self, key):
        # IMPORTANTE: usar get_blob() (que faz HEAD + popula a generation)
        # ao invés de bucket.blob() + download_as_text(). Sem isso, a SDK
        # do google-cloud-storage pode devolver um snapshot stale do conteúdo
        # — observado empiricamente em produção (Cloud Run): mesmo bucket,
        # mesma chave, gcloud storage cat retornava 205KB, mas
        # blob.download_as_text() retornava 232KB de uma versão anterior.
        # `get_blob` ancora a generation antes do download e elimina o bug.
        blob = self._bucket.get_blob(key)
        if blob is None:
            return None
        return blob.download_as_text()

    def write_text(self, key, text, content_type="text/turtle; charset=utf-8"):
        blob = self._bucket.blob(key)
        blob.upload_from_string(text, content_type=content_type)

    # ADIÇÃO local (ver LocalStateStore.read_bytes).
    def read_bytes(self, key):
        blob = self._bucket.get_blob(key)
        if blob is None:
            return None
        return blob.download_as_bytes()

    def write_bytes(self, key, data, content_type=None):
        blob = self._bucket.blob(key)
        blob.upload_from_string(
            data, content_type=content_type or self._ct_for(key)
        )

    def delete(self, key):
        blob = self._bucket.blob(key)
        if blob.exists():
            blob.delete()

    def delete_prefix(self, prefix):
        for blob in self._client.list_blobs(self.bucket_name, prefix=prefix):
            try:
                blob.delete()
            except Exception as e:  # noqa: BLE001
                # Não propaga (best-effort, igual ao LocalStateStore), mas
                # loga — silêncio total deixava blobs órfãos públicos no
                # bucket com o handler reportando sucesso.
                print(f"[gcs] falha apagando {blob.name}: {e}")

    def exists(self, key):
        return self._bucket.blob(key).exists()

    def public_url(self, key):
        # Bucket é uniform-access + publicly readable; servir via redirect
        # é muito mais eficiente que streamar via Flask.
        return f"https://storage.googleapis.com/{self.bucket_name}/{key}"

    def list_keys(self, prefix):
        return [b.name for b in self._client.list_blobs(self.bucket_name, prefix=prefix)]


def make_store_from_env(default_local_root: str | Path) -> StateStore:
    """Constrói o store apropriado a partir de variáveis de ambiente.

    STORAGE_BACKEND=gcs  →  GCSStateStore(GCS_BUCKET)
    STORAGE_BACKEND=local (default)  →  LocalStateStore(default_local_root)
    """
    import os

    backend = (os.environ.get("STORAGE_BACKEND") or "local").lower()
    if backend == "gcs":
        bucket = os.environ.get("GCS_BUCKET")
        if not bucket:
            raise RuntimeError("STORAGE_BACKEND=gcs requer GCS_BUCKET")
        return GCSStateStore(bucket)
    if backend == "local":
        return LocalStateStore(default_local_root)
    raise RuntimeError(f"STORAGE_BACKEND inválido: {backend}")
