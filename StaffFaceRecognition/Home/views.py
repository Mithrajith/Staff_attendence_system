from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.utils import timezone
from .models import Employee, Attendance
from datetime import datetime, date
import logging
import csv
from django.contrib import messages
from django.conf import settings
import os
import requests
import shutil
from dotenv import dotenv_values
import asyncio
import aiohttp
from asgiref.sync import sync_to_async
from concurrent.futures import ThreadPoolExecutor
from django.db import transaction
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import json
from requests.exceptions import RequestException
from django.shortcuts import get_object_or_404
# Add this import at the top with other imports
from django.core.paginator import Paginator

logger = logging.getLogger(__name__)

config = dotenv_values("./.env")
IP = config.get("IP", "127.0.0.1")  # Default to localhost if no IP in .env

def get_env_values(request):
    env_vars = {
        "IP": IP,
    }
    return JsonResponse(env_vars, safe = False)

from functools import wraps

def get_user_role(user):
    if not user or not user.is_authenticated:
        return 'anonymous'
    if user.username.lower() in ['system', 'kiosk'] or user.groups.filter(name='System').exists():
        return 'system'
    if user.is_superuser or user.is_staff or user.groups.filter(name='Admin').exists():
        return 'admin'
    return 'staff'

def is_admin(user):
    return get_user_role(user) == 'admin'

def is_superuser(user):
    return user.is_superuser

def admin_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        role = get_user_role(request.user)
        if role == 'admin':
            return view_func(request, *args, **kwargs)
        elif role == 'system':
            return redirect('kiosk')
        else:
            messages.warning(request, "Access restricted to administrators.")
            return redirect('my_attendance')
    return _wrapped_view

@login_required
def home_view(request):
    role = get_user_role(request.user)
    if role == 'system':
        return redirect('kiosk')
    elif role == 'staff':
        return redirect('my_attendance')
    today = date.today()

    # Get today's attendance
    today_attendance = Attendance.objects.select_related('emp').filter(date=today)

    # Calculate present/absent counts
    total_employees = Employee.objects.count()
    present_count = today_attendance.count()
    absent_count = total_employees - present_count

    # Get recent activity (check-ins and check-outs)
    recent_activity = []
    for att in today_attendance.order_by('-id')[:10]:  # Get 10 most recent attendance records
        if att.get_in_time():
            for time_in in att.get_in_time():
                recent_activity.append({
                    'emp_id': att.emp.emp_id,
                    'name': att.emp.emp_name,
                    'time': datetime.strptime(time_in, '%H:%M:%S').time(),
                    'type': 'check_in'
                })
        
        if att.get_out_time():
            for time_out in att.get_out_time():
                recent_activity.append({
                    'emp_id': att.emp.emp_id,
                    'name': att.emp.emp_name,
                    'time': datetime.strptime(time_out, '%H:%M:%S').time(),
                    'type': 'check_out'
                })
    
    # Sort by time (most recent first)
    recent_activity.sort(key=lambda x: x['time'], reverse=True)
    recent_activity = recent_activity[:10]  # Limit to 10 items

    context = {
        'present_today': present_count,
        'absent_today': absent_count,
        'total_employees': total_employees,
        'recent_activity': recent_activity
    }
    
    return render(request, 'home.html', context)

@login_required
def get_attendance(request):
    today = date.today()

    # Get today's attendance
    today_attendance = Attendance.objects.select_related('emp').filter(date=today)

    # Format attendance data
    formatted_attendance = [{
        'emp_id': att.emp.emp_id,
        'emp_name': att.emp.emp_name,
        'department': att.emp.department,
        'time_in': att.get_in_time()[0] if att.get_in_time() else '--:--',
        'time_out': att.get_out_time()[-1] if att.get_out_time() else '--:--',
        'working_hours': att.get_working_hours(),
    } for att in today_attendance]

    return JsonResponse({
        'date': today.strftime("%Y-%m-%d"),
        'attendance': formatted_attendance
    })

@admin_required
def report_view(request):
    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')
    department = request.GET.get('department')

    # Query attendance data
    attendance_query = Attendance.objects.select_related('emp').all()

    if start_date and end_date:
        attendance_query = attendance_query.filter(
            date__range=[start_date, end_date]
        )

    if department:
        attendance_query = attendance_query.filter(
            emp__department=department
        )
    # Get unique departments for filter dropdown
    departments = Employee.objects.values_list('department', flat=True).distinct()

    # Format report data
    attendance_data = [{
        'date': att.date,
        'emp_id': att.emp.emp_id,
        'emp_name': att.emp.emp_name,
        'department': att.emp.department,
        'time_in_list': att.get_in_time()[0] if att.get_in_time() else '--:--',
        'time_out_list': att.get_out_time()[-1] if att.get_out_time() else '--:--',
        'working_hours': att.get_working_hours(),
    } for att in attendance_query]

    context = {
        'attendance_data': attendance_data,
        'departments': departments,
        'start_date': start_date,
        'end_date': end_date,
        'department': department
    }
    return render(request, 'report.html', context)

@admin_required
def export_report(request):
    try:
        # Get filter parameters
        start_date = request.GET.get('start_date')
        end_date = request.GET.get('end_date')
        department = request.GET.get('department')  # Added department filter to match report_view

        # Query attendance data
        attendance_query = Attendance.objects.select_related('emp').all()

        # Apply filters if provided
        if start_date and end_date:
            try:
                # Convert string dates to datetime objects
                start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
                end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
                attendance_query = attendance_query.filter(date__range=[start_date, end_date])
            except ValueError:
                messages.error(request, 'Invalid date format. Use YYYY-MM-DD')
                return redirect('report')

        if department:
            attendance_query = attendance_query.filter(emp__department=department)

        # Check if any records exist
        if not attendance_query.exists():
            messages.warning(request, 'No attendance records found for the selected period')
            return redirect('report')

        # Prepare response
        response = HttpResponse(content_type='text/csv')
        filename = "attendance_report"
        if start_date and end_date:
            filename += f"_{start_date}_to_{end_date}"
        response['Content-Disposition'] = f'attachment; filename="{filename}.csv"'

        # Write CSV data
        writer = csv.writer(response)
        writer.writerow([
            'Date',
            'Employee ID',
                        'Employee Name',
            'Department',
            'In Time',
            'Out Time',
            'Working Hours'
        ])

        # Add attendance records
        for record in attendance_query:
            in_time = record.get_in_time()[0] if record.get_in_time() else '--:--'
            out_time = record.get_out_time()[-1] if record.get_out_time() else '--:--'

            writer.writerow([
                record.date.strftime('%Y-%m-%d'),
                record.emp.emp_id,
                record.emp.emp_name,
                record.emp.department,
                in_time,
                out_time,
                record.get_working_hours(),
            ])

        return response

    except Exception as e:
        logger.error(f"Export failed: {str(e)}", exc_info=True)
        messages.error(request, 'Failed to export report')
        return redirect('report')

def store_embeddings(db_path, output_file="backend/face_embeddings.json"):
    url = "http://127.0.0.1:5600/store_embeddings/"
    payload = {"db_path": db_path, "output_file": output_file}
    response = requests.post(url, json=payload)  # Sending as JSON body
    return response.json()


def load_embeddings(output_file="backend/face_embeddings.json"):
    url = "http://127.0.0.1:5600/load_embeddings/"
    params = {"input_file": output_file}
    response = requests.get(url, params=params)
    return response.json()

async def store_embeddings_async(db_path, output_file="backend/face_embeddings.json"):
    url = "http://127.0.0.1:5600/store_embeddings/"
    payload = {"db_path": db_path, "output_file": output_file}

    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as response:
            return await response.json()

async def process_embeddings_background(emp_id, temp_dir, media_root, embeddings_file):
    """Background task to process embeddings"""
    try:
        response = await store_embeddings_async(media_root, embeddings_file)
        if response.get('status') == 'success':
            logger.info(f"Embeddings stored successfully for {emp_id}")

            # Notify client through WebSocket (optional)
            channel_layer = get_channel_layer()
            await channel_layer.group_send(
                "admin_notifications",
                {
                    "type": "embedding_complete",
                    "message": f"Face embeddings processed for employee {emp_id}"
                }
            )

        # After embeddings are stored, trigger reload
        try:
            response = requests.post(
                "http://127.0.0.1:5600/reload-embeddings",
                timeout=5
            )
            if response.status_code != 200:
                logger.warning(f"Failed to reload embeddings: {response.status_code}")
        except requests.RequestException as e:
            logger.error(f"Error reloading embeddings: {e}")
            
    except Exception as e:
        logger.error(f"Error processing embeddings: {e}")
    finally:
        shutil.rmtree(temp_dir)

def process_images_async(temp_dir, images):
    """Process images in parallel"""
    with ThreadPoolExecutor() as executor:
        def save_image(image):
            image_path = os.path.join(temp_dir, image.name)
            with open(image_path, 'wb+') as destination:
                for chunk in image.chunks():
                    destination.write(chunk)
            return image_path

        return list(executor.map(save_image, images))

@admin_required
def manage_employees(request):
    embeddings_file = os.path.join(settings.BASE_DIR, 'backend', 'face_embeddings.json')
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'add':
            try:
                with transaction.atomic():
                    emp_id = request.POST.get('emp_id')
                    emp_name = request.POST.get('emp_name')
                    department = request.POST.get('department')

                    if Employee.objects.filter(emp_id=emp_id).exists():
                        messages.error(request, f'Employee with ID {emp_id} already exists.')
                        return redirect('manage_employees')

                    # Create employee record
                    employee = Employee.objects.create(
                        emp_id=emp_id,
                        emp_name=emp_name,
                        department=department
                    )

                    # Handle image uploads
                    images = request.FILES.getlist('images')
                    if images:
                        # Create profile pics directory if it doesn't exist
                        profile_pics_dir = os.path.join(settings.MEDIA_ROOT, 'profile_pics')
                        os.makedirs(profile_pics_dir, exist_ok=True)

                        # Save first image as profile picture with correct extension
                        profile_pic = images[0]
                        extension = os.path.splitext(profile_pic.name)[1].lower()
                        if extension not in ['.jpg', '.jpeg', '.png']:
                            extension = '.jpg'  # Default to jpg if unknown extension
                        profile_pic_path = os.path.join(profile_pics_dir, f"{emp_id}{extension}")
                        
                        # Save profile picture
                        with open(profile_pic_path, 'wb+') as destination:
                            for chunk in profile_pic.chunks():
                                destination.write(chunk)

                        # Create directory for training images
                        training_dir = os.path.join(settings.MEDIA_ROOT, emp_id)
                        os.makedirs(training_dir, exist_ok=True)

                        # Save all images (including first) for training
                        for img in images:
                            img_path = os.path.join(training_dir, img.name)
                            with open(img_path, 'wb+') as destination:
                                for chunk in img.chunks():
                                    destination.write(chunk)

                        # Process embeddings in background
                        async_to_sync(process_embeddings_background)(
                            emp_id=emp_id,
                            temp_dir=training_dir,
                            media_root=settings.MEDIA_ROOT,
                            embeddings_file=embeddings_file
                        )

                        messages.success(request, 'Employee added successfully. Face embeddings are being processed...')
                    else:
                        messages.success(request, 'Employee added successfully.')

                return redirect('manage_employees')

            except Exception as e:
                logger.error(f"Error adding employee: {str(e)}", exc_info=True)
                messages.error(request, f'Failed to add employee: {str(e)}')

        elif action == 'delete':
            try:
                emp_id = request.POST.get('emp_id')
                if not emp_id:
                    raise ValueError("Employee ID is required")
                    
                with transaction.atomic():
                    # 1. Delete employee images from media directory
                    employee_images_path = os.path.join(settings.MEDIA_ROOT, emp_id)
                    if os.path.exists(employee_images_path):
                        shutil.rmtree(employee_images_path)
                        logger.info(f"Deleted image directory for employee {emp_id}")

                    # Delete profile picture
                    profile_pics_dir = os.path.join(settings.MEDIA_ROOT, 'profile_pics')
                    for ext in ['.jpg', '.jpeg', '.png']:
                        profile_pic_path = os.path.join(profile_pics_dir, f"{emp_id}{ext}")
                        if os.path.exists(profile_pic_path):
                            os.remove(profile_pic_path)
                            logger.info(f"Deleted profile picture for employee {emp_id}")
                            break
                            
                    # 2. Delete embeddings from face_embeddings.json
                    embeddings_file = os.path.join(settings.BASE_DIR, 'backend', 'face_embeddings.json')
                    if os.path.exists(embeddings_file):
                        try:
                            with open(embeddings_file, 'r') as f:
                                embeddings_data = json.load(f)

                            if emp_id in embeddings_data:
                                del embeddings_data[emp_id]
                                
                                with open(embeddings_file, 'w') as f:
                                    json.dump(embeddings_data, f, indent=4)
                                logger.info(f"Embeddings for employee {emp_id} deleted successfully")
                        except (json.JSONDecodeError, IOError) as e:
                            logger.error(f"Failed to delete embeddings for {emp_id}: {e}")
                            
                  
                    employee = Employee.objects.filter(emp_id=emp_id).first()
                    if employee:
                        employee.delete()
                        logger.info(f"Employee {emp_id} deleted from database")
                        messages.success(request, 'Employee deleted successfully')
                    else:
                        logger.warning(f"Employee {emp_id} not found in database")
                        messages.warning(request, 'Employee not found in database')

                    # After successful deletion, reload embeddings
                    try:
                        response = requests.post(
                            "http://127.0.0.1:5600/reload-embeddings",
                            timeout=5
                        )
                        if response.status_code == 200:
                            logger.info("Face embeddings reloaded after deletion")
                        else:
                            logger.warning(f"Failed to reload embeddings: {response.status_code}")
                    except requests.RequestException as e:
                        logger.error(f"Error reloading embeddings: {e}")
                        
            except Exception as e:
                logger.error(f"Failed to delete employee: {str(e)}", exc_info=True)
                messages.error(request, f'Failed to delete employee: {str(e)}')

        # Redirect to avoid resubmission on refresh.
        return redirect('manage_employees')

    sort_by = request.GET.get('sort', 'emp_id')
    
    # Validate sort field to prevent injection
    valid_sort_fields = ['emp_id', 'emp_name', 'department', '-emp_id', '-emp_name', '-department']
    if sort_by not in valid_sort_fields:
        sort_by = 'emp_id'
    
    # Get all employees with sorting
    employees = Employee.objects.all().order_by(sort_by)
    
    # Set up pagination
    paginator = Paginator(employees, 50)  # Show 50 employees per page
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    
    
    # Process images for current page only
    for employee in page_obj:
        # Check for profile image in media/profile_pics directory
        image_formats = ['.jpg', '.jpeg', '.png']
        image_found = False
        
        for format in image_formats:
            image_path = f"profile_pics/{employee.emp_id}{format}"
            full_path = os.path.join(settings.MEDIA_ROOT, image_path)
            
            if os.path.exists(full_path):
                employee.profile_pic = image_path
                image_found = True
                break
        
        if not image_found:
            # Make sure this path exists in your static files
            employee.profile_pic = None
    
    context = {
        'employees': page_obj,
        'sort_by': sort_by,
        'total_employees': employees.count(),
    }
    return render(request, 'manage_employees.html', context)

@admin_required
def employee_detail(request, employee_id):
    """View to display detailed information for a specific employee"""
    try:
        # Get the employee object or return 404
        employee = get_object_or_404(Employee, emp_id=employee_id)
        
        # Get attendance history for this employee
        attendance_history = Attendance.objects.filter(emp=employee).order_by('-date')[:10]  # Last 10 records
        
        # Format attendance history
        formatted_attendance = [{
            'date': att.date,
            'time_in': att.get_in_time()[0] if att.get_in_time() else '--:--',
            'time_out': att.get_out_time()[-1] if att.get_out_time() else '--:--',
            'working_hours': att.get_working_hours(),
        } for att in attendance_history]
        
        context = {
            'employee': employee,
            'attendance_history': formatted_attendance,
            'total_attendance': Attendance.objects.filter(emp=employee).count(),
            'MEDIA_URL': settings.MEDIA_URL,  # Add this line
        }

        return render(request, 'employee_detail.html', context)
        
    except Exception as e:
        logger.error(f"Error in employee_detail view: {str(e)}", exc_info=True)
        messages.error(request, 'An error occurred while fetching employee details.')
        return redirect('manage_employees')

def debug_employee_detail(request, employee_id):
    print(f"Received employee_id: {employee_id}")
    return HttpResponse(f"Debug: Employee ID = {employee_id}")

@login_required
def kiosk_view(request):
    """Role 1: Dedicated System Kiosk Terminal for Check In and Check Out only."""
    role = get_user_role(request.user)
    if role == 'staff':
        messages.warning(request, "Staff members can view their personal attendance here.")
        return redirect('my_attendance')
    context = {
        'ip': IP,
        'user': request.user,
        'role': role,
    }
    return render(request, 'kiosk.html', context)

@admin_required
def settings_view(request):
    """Role 2 Admin: System, camera, and recognition configuration."""

    env_path = os.path.join(settings.BASE_DIR, '.env')
    embeddings_file = os.path.join(settings.BASE_DIR, 'backend', 'face_embeddings.json')

    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'resync_embeddings':
            try:
                # 1. Train and save embeddings for all media faces
                store_resp = requests.post("http://127.0.0.1:5600/store_embeddings/", json={
                    "db_path": settings.MEDIA_ROOT,
                    "output_file": embeddings_file
                }, timeout=45)
                # 2. Reload embeddings in recognition service memory
                resp = requests.post("http://127.0.0.1:5600/reload-embeddings", timeout=10)
                if resp.status_code == 200:
                    messages.success(request, "Face embeddings trained from stored media photos and synchronized with AI engine!")
                else:
                    messages.error(request, "Failed to re-sync embeddings with recognition service.")
            except Exception as e:
                messages.error(request, f"Error communicating with backend: {e}")
            return redirect('settings')

        elif action == 'save_settings':
            new_camera = request.POST.get('camera_index', '2').strip()
            new_ip = request.POST.get('ip', '127.0.0.1').strip()
            try:
                with open(env_path, 'w') as f:
                    f.write(f"IP='{new_ip}'\nCAMERA_INDEX={new_camera}\n")
                messages.success(request, "Settings saved successfully.")
            except Exception as e:
                messages.error(request, f"Failed to save settings: {e}")
            return redirect('settings')

    embeddings_count = 0
    if os.path.exists(embeddings_file):
        try:
            with open(embeddings_file, 'r') as f:
                embs = json.load(f)
                embeddings_count = len(embs)
        except Exception:
            pass

    env_config = dotenv_values(env_path) if os.path.exists(env_path) else {}
    camera_index = env_config.get('CAMERA_INDEX', '2')
    current_ip = env_config.get('IP', IP)

    context = {
        'total_employees': Employee.objects.count(),
        'total_attendance_records': Attendance.objects.count(),
        'embeddings_count': embeddings_count,
        'camera_index': camera_index,
        'ip': current_ip,
    }
    return render(request, 'settings.html', context)

@login_required
def my_attendance_view(request):
    """Role 3 Staff: Staff Attendance Portal - personal attendance records only."""
    role = get_user_role(request.user)
    if role == 'system':
        return redirect('kiosk')

    # Find employee associated with logged-in user
    employee = None
    if hasattr(request.user, 'employee'):
        employee = request.user.employee
    else:
        employee = Employee.objects.filter(emp_id=request.user.username).first()

    if not employee:
        if request.user.is_superuser:
            messages.info(request, "Logged in as Administrator. Showing Admin Dashboard.")
            return redirect('home')
        messages.error(request, "No faculty profile linked to your user account. Please contact administrator.")
        return render(request, 'my_attendance.html', {'employee': None, 'records': []})

    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    attendance_query = Attendance.objects.filter(emp=employee).order_by('-date')
    if start_date and end_date:
        try:
            attendance_query = attendance_query.filter(date__range=[start_date, end_date])
        except Exception:
            pass

    records = []
    total_hours_sum = 0
    total_minutes_sum = 0

    for att in attendance_query:
        in_times = att.get_in_time()
        out_times = att.get_out_time()
        in_time_str = in_times[0] if in_times else '--:--'
        out_time_str = out_times[-1] if out_times else '--:--'

        hours, minutes = att.get_total_working_hours(in_times, out_times)
        total_hours_sum += hours
        total_minutes_sum += minutes

        records.append({
            'date': att.date,
            'in_time': in_time_str,
            'out_time': out_time_str,
            'working_hours': att.get_working_hours(),
            'is_present': bool(in_times or out_times),
        })

    total_days = len(records)
    total_hours_sum += total_minutes_sum // 60
    remaining_mins = total_minutes_sum % 60
    avg_minutes_per_day = ((total_hours_sum * 60) + remaining_mins) // total_days if total_days > 0 else 0
    avg_hours = avg_minutes_per_day // 60
    avg_mins = avg_minutes_per_day % 60

    # Profile picture check
    profile_pic = None
    for ext in ['.jpg', '.jpeg', '.png']:
        pic_path = os.path.join(settings.MEDIA_ROOT, 'profile_pics', f"{employee.emp_id}{ext}")
        if os.path.exists(pic_path):
            profile_pic = f"/media/profile_pics/{employee.emp_id}{ext}"
            break

    context = {
        'employee': employee,
        'profile_pic': profile_pic,
        'records': records,
        'total_days': total_days,
        'total_working_hours': f"{total_hours_sum}h {remaining_mins}m",
        'avg_daily_hours': f"{avg_hours}h {avg_mins}m",
        'start_date': start_date or '',
        'end_date': end_date or '',
    }
    return render(request, 'my_attendance.html', context)

@login_required
def export_my_attendance(request):
    """Export logged-in staff's personal attendance records to CSV."""
    employee = getattr(request.user, 'employee', None) or Employee.objects.filter(emp_id=request.user.username).first()
    if not employee:
        messages.error(request, "Employee profile not found.")
        return redirect('my_attendance')

    start_date = request.GET.get('start_date')
    end_date = request.GET.get('end_date')

    attendance_query = Attendance.objects.filter(emp=employee).order_by('-date')
    if start_date and end_date:
        try:
            attendance_query = attendance_query.filter(date__range=[start_date, end_date])
        except Exception:
            pass

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="attendance_{employee.emp_id}.csv"'
    writer = csv.writer(response)
    writer.writerow(['Date', 'Staff ID', 'Staff Name', 'Department', 'First In', 'Last Out', 'Working Hours'])

    for att in attendance_query:
        in_time = att.get_in_time()[0] if att.get_in_time() else '--:--'
        out_time = att.get_out_time()[-1] if att.get_out_time() else '--:--'
        writer.writerow([
            att.date.strftime('%Y-%m-%d'),
            employee.emp_id,
            employee.emp_name,
            employee.department,
            in_time,
            out_time,
            att.get_working_hours()
        ])

    return response