def admin_status(request):
    """Injects is_admin into every template context."""
    if request.user.is_authenticated:
        is_admin = request.user.is_superuser or request.user.groups.filter(name="Admin").exists()
    else:
        is_admin = False
    return {"is_admin": is_admin}
