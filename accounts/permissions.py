# D:\New_GAT\accounts\permissions.py

from django.core.exceptions import PermissionDenied
from django.contrib.auth.mixins import UserPassesTestMixin
from django.contrib.auth.models import User # Import User if needed for get_queryset
from django.shortcuts import redirect # Import redirect if needed for handle_no_permission
from django.contrib import messages # Import messages if needed
from .models import UserProfile
from core.views.permissions import get_accessible_schools # Assuming this function exists

class UserManagementPermissionMixin(UserPassesTestMixin):
    """
    Checks if the current user has permission to manage other users.
    - Superuser can do anything.
    - Director can manage Teachers and Homeroom Teachers within their assigned schools.
    """
    raise_exception = True # Raise PermissionDenied instead of redirecting by default

    def get_login_url(self):
        # Redirect to the main dashboard if login is required
        return 'core:dashboard'

    def test_func(self):
        """
        Basic check: Allows Superusers and Directors to access user management views.
        """
        user = self.request.user
        # Superuser always allowed
        if user.is_superuser:
            return True

        # Allow Directors access to the list and create form
        if hasattr(user, 'profile') and user.profile.role == UserProfile.Role.DIRECTOR:
            return True

        # Deny others by default
        return False

    def dispatch(self, request, *args, **kwargs):
        """
        Handles permission checks before the view is called.
        Adds specific checks for Directors trying to edit/delete users.
        """
        user = request.user

        # Run the basic check from test_func first
        if not self.test_func():
            return self.handle_no_permission()

        # --- Additional checks specifically for Directors trying to Update/Delete ---
        # Check if 'pk' (primary key of the target user) is in the URL kwargs
        if 'pk' in kwargs and not user.is_superuser:
            target_user_pk = kwargs['pk']
            try:
                # Get the user being edited/deleted
                target_user = User.objects.select_related('profile').get(pk=target_user_pk)
            except User.DoesNotExist:
                # If target user doesn't exist, let the view handle the 404
                return super().dispatch(request, *args, **kwargs)

            # --- Director specific checks ---
            if hasattr(user, 'profile') and user.profile.role == UserProfile.Role.DIRECTOR:
                director_profile = user.profile
                target_profile = getattr(target_user, 'profile', None)

                # Check 1: Target user must have a profile and an allowed role
                allowed_roles_to_manage = [UserProfile.Role.TEACHER, UserProfile.Role.HOMEROOM_TEACHER]
                if not target_profile or target_profile.role not in allowed_roles_to_manage:
                    # Raise PermissionDenied if Director tries to edit/delete other roles
                    raise PermissionDenied("У вас нет прав для управления этим типом пользователя.")

                # Check 2: Target user's school must be one of the Director's schools
                target_school = None
                if target_profile.role == UserProfile.Role.HOMEROOM_TEACHER and target_profile.homeroom_class:
                    target_school = target_profile.homeroom_class.school
                elif target_profile.school: # For regular TEACHER
                    target_school = target_profile.school

                # Use get_accessible_schools to ensure consistency, though director_profile.schools.all() might also work
                director_schools = get_accessible_schools(user)
                if not target_school or target_school not in director_schools:
                     # Raise PermissionDenied if the school doesn't match
                    raise PermissionDenied("Вы не можете управлять пользователями из школы, к которой у вас нет доступа.")

        # If all checks pass, proceed to the view
        return super().dispatch(request, *args, **kwargs)

    def handle_no_permission(self):
        """
        Handles the case where the user does not have permission.
        Redirects to the dashboard with an error message.
        """
        messages.error(self.request, "У вас нет прав для доступа к этому разделу.")
        # Redirect to the main dashboard or another appropriate page
        return redirect('core:dashboard')