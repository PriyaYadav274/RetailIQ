from django.urls import path
from . import views

urlpatterns = [

    path('', views.login_view, name='login'),

    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('dashboard-data/', views.dashboard_data, name='dashboard_data'),

    path('customer/', views.customer_view, name='customer'),
    path('product/', views.product_view, name='product'),
    path('report/', views.report_view, name='report'),
    path('upload/', views.upload_data_view, name='upload_data'),

    path('logout/', views.logout_view, name='logout'),

    path('report/full-pdf/', views.full_pdf_report, name='full_pdf_report'),
    
    path('sales_forecasting/', views.sales_forecasting_view, name='sales_forecasting')
]