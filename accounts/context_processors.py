from .permissions import Perm, has_perm


def permissions(request):
    user = getattr(request, "user", None)
    return {"can": {perm.value: has_perm(user, perm) for perm in Perm}}
