from rest_framework import viewsets, permissions, filters, status
from rest_framework.decorators import action
from rest_framework.response import Response
from .models import Job, JobExecution
from .serializers import JobSerializer, JobExecutionSerializer
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .forms import JobForm
from django.db.models import Count
from django.http import JsonResponse


class IsOwnerOrReadOnly(permissions.BasePermission):
    """
    Custom permission to only allow owners of an object to edit it.
    """

    def has_object_permission(self, request, view, obj):
        # Read permissions are allowed to any request
        if request.method in permissions.SAFE_METHODS:
            return True

        # Write permissions are only allowed to the owner
        return obj.user == request.user


class JobViewSet(viewsets.ModelViewSet):
    serializer_class = JobSerializer
    permission_classes = [permissions.IsAuthenticated, IsOwnerOrReadOnly]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name"]
    ordering_fields = ["created_at", "deadline", "priority", "status"]
    ordering = ["-created_at"]

    def get_queryset(self):
        """
        This view returns a list of all jobs
        for the currently authenticated user.
        """
        user = self.request.user
        return Job.objects.filter(user=user)

    @action(detail=False, methods=["get"])
    def analytics(self, request):
        """
        Provide basic analytics about user's jobs
        """
        user = request.user
        jobs = Job.objects.filter(user=user)

        # Calculate analytics
        stats = {
            "total_jobs": jobs.count(),
            "by_status": {
                "pending": jobs.filter(status="pending").count(),
                "running": jobs.filter(status="running").count(),
                "completed": jobs.filter(status="completed").count(),
                "failed": jobs.filter(status="failed").count(),
            },
            "by_priority": {
                "high": jobs.filter(priority="high").count(),
                "medium": jobs.filter(priority="medium").count(),
                "low": jobs.filter(priority="low").count(),
            },
        }

        # Calculate average wait times
        completed_jobs = jobs.filter(status="completed")
        if completed_jobs.exists():
            waiting_times = []
            execution_times = []

            for job in completed_jobs:
                if job.wait_time:
                    waiting_times.append(job.wait_time)
                if job.duration:
                    execution_times.append(job.duration)

            stats["avg_wait_time"] = (
                sum(waiting_times) / len(waiting_times) if waiting_times else 0
            )
            stats["avg_execution_time"] = (
                sum(execution_times) / len(execution_times) if execution_times else 0
            )
        else:
            stats["avg_wait_time"] = 0
            stats["avg_execution_time"] = 0

        return Response(stats)

    @action(detail=True, methods=["get"])
    def executions(self, request, pk=None):
        """Get the execution history of a job"""
        job = self.get_object()
        executions = job.executions.all().order_by("-started_at")
        serializer = JobExecutionSerializer(executions, many=True)
        return Response(serializer.data)

    def update(self, request, *args, **kwargs):
        job = self.get_object()
        # Only allow updates to pending jobs
        if job.status != "pending":
            return Response(
                {"detail": "Cannot update a job that is already running or completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        job = self.get_object()
        # Only allow deletion of pending jobs
        if job.status != "pending":
            return Response(
                {"detail": "Cannot delete a job that is already running or completed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    def perform_create(self, serializer):
        """Set the user when creating a job"""
        serializer.save(user=self.request.user)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        """Get statistics about the user's jobs"""
        user_jobs = self.get_queryset()

        # Get counts by status
        status_counts = user_jobs.values("status").annotate(count=Count("status"))

        # Get counts by priority
        priority_counts = user_jobs.values("priority").annotate(count=Count("priority"))

        # Calculate average wait times
        waiting_times = []
        completed_jobs = user_jobs.filter(status="completed")
        for job in completed_jobs:
            if job.started_at and job.created_at:
                waiting_times.append((job.started_at - job.created_at).total_seconds())

        avg_wait_time = sum(waiting_times) / len(waiting_times) if waiting_times else 0

        data = {
            "status_counts": {item["status"]: item["count"] for item in status_counts},
            "priority_counts": {
                item["priority"]: item["count"] for item in priority_counts
            },
            "avg_wait_time": avg_wait_time,
            "total_jobs": user_jobs.count(),
        }

        return Response(data)


@login_required
def job_create(request):
    """
    View for creating a new job
    """
    if request.method == "POST":
        form = JobForm(request.POST)
        if form.is_valid():
            job = form.save(commit=False)
            job.user = request.user
            job.save()
            messages.success(request, f'Job "{job.name}" created successfully!')
            return redirect("jobs:job_detail", job_id=job.id)
    else:
        form = JobForm()

    return render(request, "jobs/job_create.html", {"form": form})


@login_required
def job_list(request):
    """
    View for listing all jobs for current user with sorting, filtering and analytics
    """
    # Get filter parameters
    status_filter = request.GET.get("status")
    priority_filter = request.GET.get("priority")
    sort_by = request.GET.get("sort", "-created_at")

    # Start with all user's jobs
    jobs = Job.objects.filter(user=request.user)

    # Apply filters
    if status_filter and status_filter in dict(Job.STATUS_CHOICES):
        jobs = jobs.filter(status=status_filter)
    if priority_filter and priority_filter in dict(Job.PRIORITY_CHOICES):
        jobs = jobs.filter(priority=priority_filter)

    # Apply sorting
    valid_sort_fields = {
        "name": "name",
        "-name": "-name",
        "created_at": "created_at",
        "-created_at": "-created_at",
        "priority": "priority",
        "-priority": "-priority",
        "status": "status",
        "-status": "-status",
        "deadline": "deadline",
        "-deadline": "-deadline",
    }
    if sort_by in valid_sort_fields:
        jobs = jobs.order_by(valid_sort_fields[sort_by])

    # Calculate analytics
    all_user_jobs = Job.objects.filter(user=request.user)

    # Status counts
    stats = {
        "pending_count": all_user_jobs.filter(status="pending").count(),
        "running_count": all_user_jobs.filter(status="running").count(),
        "completed_count": all_user_jobs.filter(status="completed").count(),
        "failed_count": all_user_jobs.filter(status="failed").count(),
    }

    # Priority counts
    stats.update(
        {
            "high_priority_count": all_user_jobs.filter(priority="high").count(),
            "medium_priority_count": all_user_jobs.filter(priority="medium").count(),
            "low_priority_count": all_user_jobs.filter(priority="low").count(),
        }
    )

    # Calculate average wait time for completed jobs
    completed_jobs = all_user_jobs.filter(status="completed")
    total_wait_time = 0
    wait_time_count = 0

    for job in completed_jobs:
        if job.started_at and job.created_at:
            wait_time = (job.started_at - job.created_at).total_seconds()
            total_wait_time += wait_time
            wait_time_count += 1

    stats["avg_wait_time"] = (
        round(total_wait_time / wait_time_count, 3) if wait_time_count > 0 else 0
    )

    # Calculate average execution time
    total_execution_time = 0
    execution_count = 0

    for job in completed_jobs:
        if job.duration is not None:
            total_execution_time += job.duration
            execution_count += 1

    stats["avg_execution_time"] = (
        round(total_execution_time / execution_count, 3) if execution_count > 0 else 0
    )

    # Calculate priority percentages
    total_jobs = all_user_jobs.count()
    if total_jobs > 0:
        stats.update(
            {
                "high_priority_percentage": round(
                    (stats["high_priority_count"] / total_jobs) * 100, 1
                ),
                "medium_priority_percentage": round(
                    (stats["medium_priority_count"] / total_jobs) * 100, 1
                ),
                "low_priority_percentage": round(
                    (stats["low_priority_count"] / total_jobs) * 100, 1
                ),
            }
        )
    else:
        stats.update(
            {
                "high_priority_percentage": 0,
                "medium_priority_percentage": 0,
                "low_priority_percentage": 0,
            }
        )

    context = {
        "jobs": jobs,
        "stats": stats,
        "status_filter": status_filter,
        "priority_filter": priority_filter,
        "sort_by": sort_by,
        "status_choices": Job.STATUS_CHOICES,
        "priority_choices": Job.PRIORITY_CHOICES,
    }

    return render(request, "jobs/job_list.html", context)


@login_required
def job_detail(request, job_id):
    """
    View for showing details of a specific job
    """
    job = get_object_or_404(Job, id=job_id, user=request.user)
    executions = job.executions.all().order_by("-started_at")

    return render(
        request,
        "jobs/job_detail.html",
        {
            "job": job,
            "executions": executions,
        },
    )


@login_required
def job_edit(request, job_id):
    """Edit an existing job"""
    job = get_object_or_404(Job, id=job_id, user=request.user)

    # Don't allow editing running jobs
    if job.status == "running":
        messages.error(request, "Cannot edit a running job.")
        return redirect("jobs:job_detail", job_id=job.id)

    if request.method == "POST":
        form = JobForm(request.POST, instance=job)
        if form.is_valid():
            form.save()
            messages.success(request, f'Job "{job.name}" updated successfully!')
            return redirect("jobs:job_detail", job_id=job.id)
    else:
        form = JobForm(instance=job)

    return render(
        request,
        "jobs/job_create.html",
        {
            "form": form,
            "job": job,
            "is_edit": True,
        },
    )


@login_required
def job_delete(request, job_id):
    """Delete a job"""
    job = get_object_or_404(Job, id=job_id, user=request.user)

    # Don't allow deleting running jobs
    if job.status == "running":
        messages.error(request, "Cannot delete a running job.")
        return redirect("jobs:job_detail", job_id=job.id)

    if request.method == "POST":
        job_name = job.name
        job.delete()
        messages.success(request, f'Job "{job_name}" deleted successfully!')
        return redirect("jobs:job_list")

    return render(request, "jobs/job_confirm_delete.html", {"job": job})


@login_required
def job_stats(request):
    """Get job statistics"""
    user_jobs = Job.objects.filter(user=request.user)

    # Get counts by status
    status_counts = user_jobs.values("status").annotate(count=Count("status"))

    # Get counts by priority
    priority_counts = user_jobs.values("priority").annotate(count=Count("priority"))

    # Calculate average wait times
    waiting_times = []
    completed_jobs = user_jobs.filter(status="completed")
    for job in completed_jobs:
        if job.started_at and job.created_at:
            waiting_times.append((job.started_at - job.created_at).total_seconds())

    avg_wait_time = sum(waiting_times) / len(waiting_times) if waiting_times else 0

    data = {
        "status_counts": {item["status"]: item["count"] for item in status_counts},
        "priority_counts": {
            item["priority"]: item["count"] for item in priority_counts
        },
        "avg_wait_time": avg_wait_time,
        "total_jobs": user_jobs.count(),
    }

    return JsonResponse(data)


class JobExecutionViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for viewing job executions.
    """

    serializer_class = JobExecutionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        """Only return executions for jobs belonging to the current user"""
        return JobExecution.objects.filter(job__user=self.request.user)


@login_required
def job_execution_list(request):
    """
    View for listing all job executions with sorting and filtering
    """
    # Get filter parameters
    job_filter = request.GET.get("job")
    success_filter = request.GET.get("success")
    sort_by = request.GET.get("sort", "-started_at")  # Default sort by start time desc

    # Get all executions for the user's jobs
    executions = JobExecution.objects.filter(job__user=request.user).select_related(
        "job"
    )

    # Apply filters
    if job_filter:
        executions = executions.filter(job__id=job_filter)
    if success_filter is not None:
        executions = executions.filter(success=success_filter == "true")

    # Apply sorting
    valid_sort_fields = {
        "started_at": "started_at",
        "-started_at": "-started_at",
        "completed_at": "completed_at",
        "-completed_at": "-completed_at",
        "execution_time": "execution_time",
        "-execution_time": "-execution_time",
        "job__name": "job__name",
        "-job__name": "-job__name",
    }
    if sort_by in valid_sort_fields:
        executions = executions.order_by(valid_sort_fields[sort_by])

    # Get all jobs for the filter dropdown
    user_jobs = Job.objects.filter(user=request.user)

    context = {
        "executions": executions,
        "job_filter": job_filter,
        "success_filter": success_filter,
        "sort_by": sort_by,
        "user_jobs": user_jobs,
    }

    return render(request, "jobs/job_execution_list.html", context)
