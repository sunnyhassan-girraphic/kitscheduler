from django.contrib.auth import authenticate, login
from django.shortcuts import redirect, render


def login_view(request):
    if request.user.is_authenticated:
        return redirect("/")

    error = None
    if request.method == "POST":
        username = request.POST.get("username", "").strip()
        password = request.POST.get("password", "")
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            next_url = request.POST.get("next") or request.GET.get("next") or "/"
            return redirect(next_url)
        else:
            error = "Invalid username or password."

    next_url = request.GET.get("next", "")
    return render(request, "inventory/login.html", {"error": error, "next": next_url})
