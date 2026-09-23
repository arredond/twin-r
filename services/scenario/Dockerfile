# Lambda container image for the scenario function.
#
# A zip-based Lambda deployment isn't viable here: the scenario package
# depends on openquake.hazardlib (pulls in numpy/scipy/numba/...), well over
# the 250MB unzipped zip-Lambda limit. A container image has no such limit.
#
# Built with the *repo root* as context (infra/stacks/twin_r_stack.py's
# `DockerImageCode.from_image_asset(str(REPO_ROOT), file="services/scenario/
# Dockerfile")`), not services/scenario/ alone -- this also needs
# services/tiles (results_store.py, imported by handler.py's compute path
# to write the S3 results the tiles Lambda reads back), a sibling directory
# Docker can't see if the build context is scoped to services/scenario/.
# All COPY paths below are relative to the repo root as a result.
FROM public.ecr.aws/lambda/python:3.12

WORKDIR ${LAMBDA_TASK_ROOT}

# fiona's manylinux wheel dynamically links libexpat.so.1 rather than
# bundling it (expat is one of the "assume it's already on the host"
# libraries under manylinux's perennial policy) -- this minimal base image
# doesn't ship it, so importing fiona fails at runtime (not build time)
# with a bare `ImportError: libexpat.so.1: cannot open shared object
# file`, confirmed by actually running the built image.
RUN dnf install -y expat && dnf clean all

COPY services/scenario/pyproject.toml ./
COPY services/scenario/src/ ./src/
COPY services/tiles/pyproject.toml ./tiles_pkg/
COPY services/tiles/src/ ./tiles_pkg/src/

# openquake.hazardlib isn't published as its own PyPI package -- it only
# ships bundled inside the `openquake.engine` distribution, whose declared
# dependencies pull in that package's *own* web/reporting/DB stack (django,
# django_cors_headers, docutils) plus a hard-pinned `gdal~=3.13.1` (no
# prebuilt wheels -- always compiles against a system libgdal + gdal-config
# of the exact matching version, which this base image doesn't have).
# None of that is on this service's actual import path -- confirmed no
# `django`/`gdal`/`osgeo` import anywhere under openquake/hazardlib, and
# services/scenario only ever imports from openquake.hazardlib.* (see
# ground_motion.py/surface.py). `--no-deps` on openquake.engine skips
# resolving its declared dependency graph entirely (which is what would
# otherwise try, and fail, to build `gdal` from source); the explicit list
# below is the actual runtime subset hazardlib needs, verified by
# installing this exact combination in an isolated venv and importing
# every module services/scenario touches, including the eager
# `openquake.hazardlib.gsim` package-level import that loads every GSim
# submodule (the same surface that needs `fiona`, for gsim/zhao_2016.py).
RUN pip install --no-cache-dir --no-deps "openquake.engine>=3.19" && \
    pip install --no-cache-dir \
        "numpy>=2.2.6" "scipy>=1.7" "pandas>=2.2.3" "h5py>=3.13" \
        "decorator~=5.1.1" "numba~=0.61.2" "alpha_shapes~=1.1.0" \
        "pyproj~=3.7.2" "geopandas~=1.1.4" "fiona~=1.10.1" \
        "h3>=4.4" "toml~=0.10.2" "pyzmq~=26.2.0" psutil requests \
        "pillow~=12.2.0" duckdb pyarrow fastapi uvicorn && \
    pip install --no-cache-dir --no-deps ./tiles_pkg && \
    pip install --no-cache-dir --no-deps .

CMD ["scenario.handler.handler"]
