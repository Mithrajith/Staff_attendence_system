from django.db import models
from django.utils import timezone
from datetime import datetime, timedelta
from django.contrib.auth.models import User

class Employee(models.Model):
    emp_id = models.CharField(max_length=10, primary_key=True)
    emp_name = models.CharField(max_length=100)
    department = models.CharField(max_length=50)
    email = models.EmailField(max_length=100, blank=True, null=True)
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True, related_name='employee')
    
    def __str__(self):
        return f"{self.emp_id} - {self.emp_name}"

class Attendance(models.Model):
    id = models.AutoField(primary_key=True)
    emp = models.ForeignKey(Employee, on_delete=models.CASCADE)
    date = models.DateField(default=timezone.now)
    time_in_list = models.TextField(blank=True, null=True)
    time_out_list = models.TextField(blank=True, null=True)
    
    def get_in_time(self):
        return self.time_in_list.split(',') if self.time_in_list else []
        
    def get_out_time(self):
        return self.time_out_list.split(',') if self.time_out_list else []
    
    from datetime import datetime, timedelta

    def calculate_working_hours(self, in_time_str, out_time_str):
        """
        Calculate working hours from in_time and out_time strings
        Returns tuple of (hours, minutes)
        """
        try:
            # Try parsing with seconds first (HH:MM:SS)
            try:
                in_time = datetime.strptime(in_time_str.strip(), '%H:%M:%S')
                out_time = datetime.strptime(out_time_str.strip(), '%H:%M:%S')
            except ValueError:
                # If that fails, try without seconds (HH:MM)
                in_time = datetime.strptime(in_time_str.strip(), '%H:%M')
                out_time = datetime.strptime(out_time_str.strip(), '%H:%M')
            
            # Handle case where out_time is before in_time (midnight crossing)
            if out_time < in_time:
                out_time = out_time.replace() + timedelta(days=1)
            
            # Calculate time difference
            time_diff = out_time - in_time
            
            # Extract hours and minutes
            total_minutes = time_diff.total_seconds() / 60
            hours = int(total_minutes // 60)
            minutes = int(total_minutes % 60)
            
            return hours, minutes
        except ValueError as e:
            print(f"Error parsing time: {e}")
            return 0, 0
    def format_working_hours(self, hours, minutes):
        """
        Format working hours into a string
        """
        if hours == 0 and minutes == 0:
            return "0h 0m"
        elif hours == 0:
            return f"{minutes}m"
        elif minutes == 0:
            return f"{hours}h"
        else:
            return f"{hours}h {minutes}m"

    def get_total_working_hours(self, time_in_list, time_out_list):
        """
        Calculate total working hours from lists of check-in and check-out times
        Returns tuple of (total_hours, total_minutes)
        """
        total_hours = 0
        total_minutes = 0
        
        # Get first check-in and last check-out
        if time_in_list and time_out_list:
            first_in = time_in_list[0].strip()
            last_out = time_out_list[-1].strip()
            
            hours, minutes = self.calculate_working_hours(first_in, last_out)
            total_hours += hours
            total_minutes += minutes
        
        # Adjust if total minutes >= 60
        if total_minutes >= 60:
            additional_hours = total_minutes // 60
            total_hours += additional_hours
            total_minutes = total_minutes % 60
            
        return total_hours, total_minutes
    def get_working_hours(self):
        in_times = self.get_in_time()
        out_times = self.get_out_time()
        
        if not in_times or not out_times:
            return "0h 0m"
        
        hours, minutes = self.get_total_working_hours(in_times, out_times)
        return self.format_working_hours(hours, minutes)

    class Meta:
        ordering = ['-date', 'emp__emp_name']
    
    def __str__(self):
        return f"{self.emp.emp_name} - {self.date}"