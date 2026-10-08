from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('signup/', views.signup_step1_view, name='signup'),
    path('signup/capture/', views.signup_capture_view, name='signup_capture'),
    path('signup/complete/', views.signup_complete_view, name='signup_complete'),
]