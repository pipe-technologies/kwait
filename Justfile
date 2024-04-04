set ignore-comments
set fallback

install:
    poetry install

lint: install
    poetry run pyright
    poetry run pylint kwait test
    just _check_format

format: install
    poetry run isort --atomic kwait test
    poetry run black --quiet kwait test

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
    poetry run pytest
