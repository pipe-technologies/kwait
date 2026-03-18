set ignore-comments
set fallback

install:
    uv sync

lint: install
    uv run pyright
    uv run pylint kwait
    just _check_format

format: install
    uv run isort --atomic src test
    uv run black --quiet src test

_check_format:
    before=$(git diff | cksum); \
    just format; \
    after=$(git diff | cksum); \
    if [[ $before != $after ]]; then \
        echo "Code formatting errors found in files:"; \
        git ls-files --modified; \
        echo; \
        echo "Run 'just format'"; \
        exit 1; \
    fi

test: install
    uv run pytest
