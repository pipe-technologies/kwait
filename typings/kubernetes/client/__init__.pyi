# the kubernetes client doesn't provide type hints
# (https://github.com/kubernetes-client/python/issues/225), and I
# couldn't get any of the third-party stub packages (e.g.,
# https://pypi.org/project/kubernetes-stubs-elephant-fork/) to
# work. Workaround borrowed from
# https://github.com/microsoft/pyright/issues/945#issuecomment-686292537
def __getattr__(name) -> Any: ...
