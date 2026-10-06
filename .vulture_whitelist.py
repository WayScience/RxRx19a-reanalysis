# Vulture whitelist: reserved-for-future-use parameters and false
# positives, in vulture's own --make-whitelist bare-reference format.

# rerx.cytotable: imported only for its TYPE_CHECKING-time annotation
# (parsl.Config); vulture's static analysis can't see that use.
parsl

# rerx.manifest.build_source_manifest: reserved for a threaded hashing
# implementation (currently serial); see the function's own docstring.
hash_workers

# rerx.morphem.morphem_command: recorded into run.json by the caller, not
# read by the function itself, but kept as an explicit parameter so the
# command-building and version-recording concerns stay visible together.
revision

# tests/test_cellprofiler.py fake_run(): signature must match
# subprocess.run's keyword arguments for the monkeypatch to work, even
# though the fake implementation doesn't read these.
capture_output
check
text

# tests/test_embeddings.py + tests/test_metadata.py fakes: signatures
# must match requests.get / Response.iter_content for the monkeypatch
# and streaming protocol to work, even though the fakes don't read
# these.
chunk_size
stream
