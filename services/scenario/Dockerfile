# Lambda container image for the scenario function.
#
# A zip-based Lambda deployment isn't viable here: the scenario package
# depends on openquake.hazardlib (pulls in numpy/scipy/numba/...), well over
# the 250MB unzipped zip-Lambda limit. A container image has no such limit.
#
# Not yet built/pushed against a real account -- see infra/stacks/twin_r_stack.py.
FROM public.ecr.aws/lambda/python:3.12

WORKDIR ${LAMBDA_TASK_ROOT}

COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

CMD ["scenario.handler.handler"]
