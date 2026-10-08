import os
import json
import requests
from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib import messages
from django.http import JsonResponse
from django.db import transaction
from Home.models import Employee

def get_user_role(user):
    if not user or not user.is_authenticated:
        return 'anonymous'
    if user.username.lower() in ['system', 'kiosk'] or user.groups.filter(name='System').exists():
        return 'system'
    if user.is_superuser or user.is_staff or user.groups.filter(name='Admin').exists():
        return 'admin'
    return 'staff'

def anonymous_required(function=None):
    def wrapper(request, *args, **kwargs):
        if request.user.is_authenticated:
            role = get_user_role(request.user)
            if role == 'system':
                return redirect('kiosk')
            elif role == 'admin':
                return redirect('home')
            else:
                return redirect('my_attendance')
        return function(request, *args, **kwargs)
    return wrapper

@anonymous_required
def login_view(request):
    if request.method == 'POST':
        login_input = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')

        # Check if login_input is an email
        username_to_auth = login_input
        user_by_email = User.objects.filter(email__iexact=login_input).first()
        if user_by_email:
            username_to_auth = user_by_email.username

        user = authenticate(request, username=username_to_auth, password=password)
        if user is not None:
            login(request, user)
            role = get_user_role(user)
            if role == 'system':
                return redirect('kiosk')
            elif role == 'admin':
                return redirect('home')
            else:
                return redirect('my_attendance')
        else:
            messages.error(request, 'Invalid Username / Staff ID or Password.')
    return render(request, 'login.html')

def logout_view(request):
    logout(request)
    return redirect('login')

@anonymous_required
def signup_step1_view(request):
    """Step 1 of Staff Sign Up: Personal & Account Details."""
    if request.method == 'POST':
        staff_id = request.POST.get('staff_id', '').strip()
        staff_name = request.POST.get('staff_name', '').strip()
        department = request.POST.get('department', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        confirm_password = request.POST.get('confirm_password', '')

        # Validations
        if not all([staff_id, staff_name, department, email, password]):
            messages.error(request, 'All fields are required.')
            return render(request, 'signup_step1.html', request.POST)

        if len(password) < 8:
            messages.error(request, 'Password must be at least 8 characters or digits long.')
            return render(request, 'signup_step1.html', request.POST)

        if password != confirm_password:
            messages.error(request, 'Passwords do not match.')
            return render(request, 'signup_step1.html', request.POST)

        if User.objects.filter(username__iexact=staff_id).exists():
            messages.error(request, f'Staff ID "{staff_id}" is already registered. Please login.')
            return render(request, 'signup_step1.html', request.POST)

        if Employee.objects.filter(emp_id__iexact=staff_id).exists():
            messages.error(request, f'Employee record for ID "{staff_id}" already exists. Please contact admin.')
            return render(request, 'signup_step1.html', request.POST)

        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, f'Email "{email}" is already registered.')
            return render(request, 'signup_step1.html', request.POST)

        # Store in session and proceed to Step 2
        request.session['signup_data'] = {
            'staff_id': staff_id,
            'staff_name': staff_name,
            'department': department,
            'email': email,
            'password': password
        }
        return redirect('signup_capture')

    # Pre-populate if session data exists
    signup_data = request.session.get('signup_data', {})
    return render(request, 'signup_step1.html', signup_data)

@anonymous_required
def signup_capture_view(request):
    """Step 2 of Staff Sign Up: Instructions and 10 Browser Camera Captures."""
    signup_data = request.session.get('signup_data')
    if not signup_data:
        messages.warning(request, 'Please complete Step 1 first.')
        return redirect('signup')

    context = {
        'staff_id': signup_data['staff_id'],
        'staff_name': signup_data['staff_name'],
        'department': signup_data['department'],
        'email': signup_data['email']
    }
    return render(request, 'signup_capture.html', context)

from django.views.decorators.csrf import csrf_exempt

@csrf_exempt
def signup_complete_view(request):
    """AJAX endpoint to receive 10 captured images, extract embeddings, create User and Employee."""
    if request.method != 'POST':
        return JsonResponse({'status': 'error', 'message': 'Invalid request method.'}, status=405)

    signup_data = request.session.get('signup_data')
    if not signup_data:
        return JsonResponse({'status': 'error', 'message': 'Session expired. Please restart signup.'}, status=400)

    try:
        data = json.loads(request.body.decode('utf-8'))
        images = data.get('images', [])

        if not images or len(images) < 10:
            return JsonResponse({
                'status': 'error',
                'message': f'Expected 10 face captures, but received {len(images)}. Please capture all 10 images.'
            }, status=400)

        staff_id = signup_data['staff_id']
        staff_name = signup_data['staff_name']
        department = signup_data['department']
        email = signup_data['email']
        password = signup_data['password']

        # Call FastAPI backend to process face embeddings and save images
        try:
            fastapi_url = "http://127.0.0.1:5600/register-staff-faces/"
            response = requests.post(fastapi_url, json={
                'emp_id': staff_id,
                'images': images
            }, timeout=30)

            if response.status_code != 200:
                err_detail = "Failed to process face images. Ensure face is clearly visible."
                try:
                    err_detail = response.json().get('detail', err_detail)
                except Exception:
                    pass
                return JsonResponse({'status': 'error', 'message': err_detail}, status=400)

        except requests.RequestException as e:
            return JsonResponse({
                'status': 'error',
                'message': f'Recognition service connection error: {str(e)}'
            }, status=500)

        # Create Django User and Employee records atomically
        with transaction.atomic():
            user = User.objects.create_user(
                username=staff_id,
                email=email,
                password=password,
                first_name=staff_name
            )
            employee = Employee.objects.create(
                emp_id=staff_id,
                emp_name=staff_name,
                department=department,
                email=email,
                user=user
            )

        # Clear session signup data
        request.session.pop('signup_data', None)

        # Log the user in automatically
        authenticated_user = authenticate(request, username=staff_id, password=password)
        if authenticated_user:
            login(request, authenticated_user)

        return JsonResponse({
            'status': 'success',
            'message': 'Registration completed successfully! Welcome to the Staff Portal.',
            'redirect_url': '/my-attendance/'
        })

    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': 'Invalid JSON data.'}, status=400)
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': f'Registration failed: {str(e)}'}, status=500)