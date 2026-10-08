from django.urls import path
from . import views

urlpatterns = [
    # Role 1: System Kiosk (Check-in and Check-out only)
    path('kiosk/', views.kiosk_view, name='kiosk'),
    path('terminal/', views.kiosk_view, name='terminal'),
    
    # Role 2: Admin Dashboard & Management
    path('', views.home_view, name='home'),
    path('dashboard/', views.home_view, name='dashboard'),
    path('manage-employees/', views.manage_employees, name='manage_employees'),
    path('report/', views.report_view, name='report'),
    path('export-report/', views.export_report, name='export_report'),
    path('settings/', views.settings_view, name='settings'),
    path('store-embeddings/', views.store_embeddings, name='store_embeddings'),
    path('employee/<str:employee_id>/', views.employee_detail, name='employee_detail'),
    path('debug-employee/<int:employee_id>/', views.debug_employee_detail, name='debug_employee_detail'),
    path('get_attendace/', views.get_attendance, name='get_attendace'),
    path('api/get_env/', views.get_env_values, name='get_env_values'),

    # Role 3: Staff Portal (My Attendance Only)
    path('my-attendance/', views.my_attendance_view, name='my_attendance'),
    path('my-attendance/export/', views.export_my_attendance, name='export_my_attendance'),
]