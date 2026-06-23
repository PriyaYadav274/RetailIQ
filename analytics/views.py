from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.db import connection
from .models import Admin, RetailRaw
import json
import pandas as pd
from datetime import date
from reportlab.pdfgen import canvas
from statsmodels.tsa.arima.model import ARIMA
from sklearn.linear_model import LinearRegression
from sklearn.cluster import KMeans
from sklearn.linear_model import LogisticRegression
import numpy as np



#  LOGIN VIEW
def login_view(request):
    error = None

    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')

        try:
            admin = Admin.objects.get(email=email, password=password)
            request.session['admin_id'] = admin.pk
            return redirect('upload_data')
        except Admin.DoesNotExist:
            error = "Invalid Email or Password"

    return render(request, 'analytics/login.html', {'error': error})


#  LOGOUT VIEW
def logout_view(request):
    request.session.flush()
    return redirect('login')


#  CHECK LOGIN
def check_login(request):
    return request.session.get('admin_id')


#  DASHBOARD PAGE
def dashboard_view(request):
    if not check_login(request):
        return redirect('login')

    return render(request, 'analytics/dashboard.html')


#  DASHBOARD 
def dashboard_data(request):
    with connection.cursor() as cursor:

        cursor.execute("SELECT IFNULL(SUM(total_amount),0) FROM orders")
        total_revenue = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM orders")
        total_orders = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM customers")
        total_customers = cursor.fetchone()[0]

        cursor.execute("""
            SELECT IFNULL(SUM(oi.subtotal - (p.cost_price * oi.quantity)),0)
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
        """)
        total_profit = cursor.fetchone()[0]

        cursor.execute("""
            SELECT MONTH(order_date), SUM(total_amount)
            FROM orders
            GROUP BY MONTH(order_date)
            ORDER BY MONTH(order_date)
        """)
        monthly_data = cursor.fetchall()

        cursor.execute("""
            SELECT p.product_name, SUM(oi.quantity)
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
            GROUP BY p.product_name
            ORDER BY SUM(oi.quantity) DESC
            LIMIT 5
        """)
        top_products = cursor.fetchall()

        cursor.execute("""
            SELECT p.category, SUM(oi.subtotal)
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
            GROUP BY p.category
        """)
        category_data = cursor.fetchall()

        cursor.execute("""
            SELECT order_id, total_amount, order_date
            FROM orders
            ORDER BY order_date DESC
            LIMIT 5
        """)
        recent_orders = cursor.fetchall()

    return JsonResponse({
        "kpi": {
            "revenue": total_revenue,
            "orders": total_orders,
            "customers": total_customers,
            "profit": total_profit
        },
        "monthly": monthly_data,
        "top_products": top_products,
        "category": category_data,
        "recent_orders": recent_orders
    })


#  SALES AND FORCASTING

def sales_forecasting_view(request):
    if not check_login(request):
        return redirect('login')

    with connection.cursor() as cursor:

        # ================= SALES =================

        # Sales Over Time
        cursor.execute("""
            SELECT DATE(order_date), SUM(total_amount)
            FROM orders
            GROUP BY DATE(order_date)
            ORDER BY DATE(order_date)
        """)
        sales_raw = cursor.fetchall()

        sales_trends = [[str(r[0]), float(r[1])] for r in sales_raw]

        # Category Sales
        cursor.execute("""
            SELECT p.category, SUM(oi.subtotal)
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
            GROUP BY p.category
        """)
        category_raw = cursor.fetchall()
        category_data = [[r[0], float(r[1])] for r in category_raw]

        #  Region Sales
        cursor.execute("""
            SELECT c.location, SUM(o.total_amount)
            FROM orders o
            JOIN customers c ON o.customer_id = c.customer_id
            GROUP BY c.location
        """)
        region_raw = cursor.fetchall()
        region_data = [[r[0], float(r[1])] for r in region_raw]

    # =================  ARIMA FORECAST =================

    sales_values = [r[1] for r in sales_trends]

    forecast_values = []
    future_months = []

    if len(sales_values) > 10:
        try:
            model = ARIMA(sales_values, order=(2,1,2))
            model_fit = model.fit()

            forecast = model_fit.forecast(steps=6)
            forecast_values = [float(v) for v in forecast]

            future_months = [f"Month {i}" for i in range(1,7)]

        except:
            forecast_values = []
            future_months = []

    # ================= 🏷️ CATEGORY FORECAST =================

    category_forecast = {}

    for cat in category_data:

        cat_name = cat[0]

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT DATE(o.order_date), oi.subtotal
                FROM order_items oi
                JOIN orders o ON oi.order_id = o.order_id
                JOIN products p ON oi.product_id = p.product_id
                WHERE p.category = %s
                ORDER BY o.order_date
            """, [cat_name])

            rows = cursor.fetchall()

        if len(rows) < 5:
            continue

        y = np.array([float(r[1]) for r in rows])
        X = np.arange(len(y)).reshape(-1,1)

        model = LinearRegression()
        model.fit(X, y)

        future_X = np.arange(len(y), len(y)+5).reshape(-1,1)
        preds = model.predict(future_X)

        category_forecast[cat_name] = [float(v) for v in preds]

    # ================= CONTEXT =================

    context = {
        "sales_trends": json.dumps(sales_trends),
        "category_data": json.dumps(category_data),
        "region_data": json.dumps(region_data),

        "forecast_values": json.dumps(forecast_values),
        "future_months": json.dumps(future_months),

        "category_forecast": json.dumps(category_forecast),
    }

    return render(request, "analytics/sales_forecasting.html", context)


#  CUSTOMER ANALYTICS
def customer_view(request):
    if not check_login(request):
        return redirect('login')

    with connection.cursor() as cursor:

        # ================= RFM =================
        cursor.execute("""
            SELECT 
                customer_id,
                MAX(order_date),
                COUNT(order_id),
                SUM(total_amount)
            FROM orders
            GROUP BY customer_id
        """)
        rfm_raw = cursor.fetchall()

        rfm_data = []
        X = []  # for ML

        for row in rfm_raw:
            freq = int(row[2])
            monetary = float(row[3])

            rfm_data.append({
                "customer_id": row[0],
                "last_order": str(row[1]),
                "frequency": freq,
                "monetary": monetary
            })

            # ML input (frequency + monetary)
            X.append([freq, monetary])

        # ================= NEW VS RETURNING =================
        cursor.execute("""
            SELECT customer_id, COUNT(order_id)
            FROM orders
            GROUP BY customer_id
        """)
        nv_raw = cursor.fetchall()

        new_customers = 0
        returning_customers = 0

        for row in nv_raw:
            if row[1] == 1:
                new_customers += 1
            else:
                returning_customers += 1

        # ================= CLV =================
        cursor.execute("""
            SELECT customer_id, SUM(total_amount)
            FROM orders
            GROUP BY customer_id
        """)
        clv_raw = cursor.fetchall()

        clv_data = [
            [row[0], float(row[1])]
            for row in clv_raw
        ]

    # ================= ML: SEGMENTATION (K-Means) =================
    segment_counts = [0, 0, 0]

    if len(X) >= 3:
        kmeans = KMeans(n_clusters=3, random_state=42)
        labels = kmeans.fit_predict(X)

        for l in labels:
            segment_counts[l] += 1

    # ================= ML: CHURN (Logistic Regression) =================
    churn_result = {"low": 0, "high": 0}

    if len(X) >= 2:
        X_np = np.array(X)

        # Dummy target (for demo)
        # low frequency → high churn
        y = np.array([1 if x[0] < 2 else 0 for x in X_np])

        model = LogisticRegression()
        model.fit(X_np, y)

        preds = model.predict(X_np)

        churn_result["high"] = int(sum(preds))
        churn_result["low"] = int(len(preds) - sum(preds))

    # ================= ASSOCIATION RULES (SIMPLE) =================
    # (Real Apriori needs mlxtend, keeping simple for now)
    rules = [
        {"if": "High Frequency", "then": "High Spending", "confidence": "80%"},
        {"if": "Low Frequency", "then": "High Churn Risk", "confidence": "75%"},
        {"if": "Returning Customer", "then": "Higher CLV", "confidence": "85%"}
    ]

    # ================= FINAL RESPONSE =================
    return render(request, 'analytics/customer.html', {
        "rfm_data": json.dumps(rfm_data),
        "new_customers": new_customers,
        "returning_customers": returning_customers,
        "clv_data": json.dumps(clv_data),

        #  NEW (important for frontend)
        "segment_ml": json.dumps(segment_counts),
        "churn_ml": json.dumps(churn_result),
        "rules": json.dumps(rules)
    })


#  PRODUCT ANALYTICS
def product_view(request):
    if not check_login(request):
        return redirect('login')

    import json

    seasonal_products = []  #  always initialize

    with connection.cursor() as cursor:

        #  Inventory
        cursor.execute("""
            SELECT product_name, stock_quantity
            FROM products
        """)
        inventory = [
            [row[0], int(row[1] or 0)]
            for row in cursor.fetchall()
        ]

        #  Low Stock
        cursor.execute("""
            SELECT product_name, stock_quantity
            FROM products
            WHERE stock_quantity < 10
        """)
        low_stock = [
            [row[0], int(row[1] or 0)]
            for row in cursor.fetchall()
        ]

        #  Best Products
        cursor.execute("""
            SELECT p.product_name, SUM(oi.quantity)
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
            GROUP BY p.product_name
            ORDER BY SUM(oi.quantity) DESC
            LIMIT 5
        """)
        best_products = [
            [row[0], int(row[1] or 0)]
            for row in cursor.fetchall()
        ]

        #  Worst Products
        cursor.execute("""
            SELECT p.product_name, SUM(oi.quantity)
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
            GROUP BY p.product_name
            ORDER BY SUM(oi.quantity) ASC
            LIMIT 5
        """)
        worst_products = [
            [row[0], int(row[1] or 0)]
            for row in cursor.fetchall()
        ]

        # Profit Margin
        cursor.execute("""
            SELECT product_name, (selling_price - cost_price)
            FROM products
        """)
        profit_data = [
            [row[0], float(row[1] or 0)]
            for row in cursor.fetchall()
        ]

        #  SEASON BASED BEST PRODUCTS (FIXED)
        cursor.execute("""
            SELECT 
                CASE 
                    WHEN MONTH(o.order_date) IN (12,1,2) THEN 'Winter'
                    WHEN MONTH(o.order_date) IN (3,4,5) THEN 'Summer'
                    WHEN MONTH(o.order_date) IN (6,7,8,9) THEN 'Monsoon'
                    ELSE 'Autumn'
                END AS season,
                p.product_name,
                SUM(oi.quantity) AS total_sales
            FROM orders o
            JOIN order_items oi ON o.order_id = oi.order_id
            JOIN products p ON oi.product_id = p.product_id
            GROUP BY season, p.product_name
            ORDER BY season, total_sales DESC
        """)

        seasonal_raw = cursor.fetchall()

        #  pick BEST product per season
        season_dict = {}

        for season, product, sales in seasonal_raw:
            if season not in season_dict:
                season_dict[season] = [season, product, int(sales or 0)]

        seasonal_products = list(season_dict.values())

    # 🧪 DEBUG (outside cursor but inside function)
    print("SEASON DATA:", seasonal_products)

    return render(request, 'analytics/product.html', {
        "inventory": json.dumps(inventory),
        "low_stock": json.dumps(low_stock),
        "best_products": json.dumps(best_products),
        "worst_products": json.dumps(worst_products),
        "profit_data": json.dumps(profit_data),
        "seasonal_products": json.dumps(seasonal_products)
    })


def report_view(request):
    if not check_login(request):
        return redirect('login')

    with connection.cursor() as cursor:

        # KPI DATA
        cursor.execute("SELECT IFNULL(SUM(total_amount),0) FROM orders")
        total_revenue = float(cursor.fetchone()[0] or 0)

        cursor.execute("SELECT COUNT(*) FROM orders")
        total_orders = int(cursor.fetchone()[0] or 0)

        cursor.execute("SELECT COUNT(*) FROM customers")
        total_customers = int(cursor.fetchone()[0] or 0)

        cursor.execute("""
            SELECT IFNULL(SUM(oi.subtotal - (p.cost_price * oi.quantity)),0)
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
        """)
        total_profit = float(cursor.fetchone()[0] or 0)

        # BEST SALES MONTH
        cursor.execute("""
            SELECT MONTHNAME(order_date), SUM(total_amount) as total_sales
            FROM orders
            GROUP BY MONTH(order_date), MONTHNAME(order_date)
            ORDER BY total_sales DESC
            LIMIT 1
        """)
        best_month_raw = cursor.fetchone()
        best_month = best_month_raw[0] if best_month_raw else "N/A"

        # BEST PRODUCT
        cursor.execute("""
            SELECT p.product_name, SUM(oi.quantity) as total_qty
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
            GROUP BY p.product_name
            ORDER BY total_qty DESC
            LIMIT 1
        """)
        top_product_raw = cursor.fetchone()
        predicted_top_product = top_product_raw[0] if top_product_raw else "N/A"

        # BEST CATEGORY
        cursor.execute("""
            SELECT p.category, SUM(oi.subtotal) as total_sales
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
            GROUP BY p.category
            ORDER BY total_sales DESC
            LIMIT 1
        """)
        best_category_raw = cursor.fetchone()
        predicted_top_category = best_category_raw[0] if best_category_raw else "N/A"

        # PREDICTED SALES
        cursor.execute("""
            SELECT AVG(monthly_total)
            FROM (
                SELECT SUM(total_amount) as monthly_total
                FROM orders
                GROUP BY MONTH(order_date)
            ) as monthly_sales
        """)
        predicted_sales = float(cursor.fetchone()[0] or 0)

        # PREDICTED GROWTH %
        cursor.execute("""
            SELECT 
                (
                    (
                        SELECT SUM(total_amount)
                        FROM orders
                        WHERE MONTH(order_date) = MONTH(CURDATE())
                    )
                    -
                    (
                        SELECT SUM(total_amount)
                        FROM orders
                        WHERE MONTH(order_date) = MONTH(CURDATE()) - 1
                    )
                )
        """)
        growth_raw = cursor.fetchone()[0]

        predicted_growth = round(float(growth_raw or 0), 2)

        # MONTHLY SALES CHART
        cursor.execute("""
            SELECT MONTHNAME(order_date), SUM(total_amount)
            FROM orders
            GROUP BY MONTH(order_date), MONTHNAME(order_date)
            ORDER BY MONTH(order_date)
        """)
        monthly_sales_raw = cursor.fetchall()

        monthly_sales_labels = [row[0] for row in monthly_sales_raw]
        monthly_sales_values = [float(row[1]) for row in monthly_sales_raw]

        # MONTHLY PROFIT CHART
        cursor.execute("""
            SELECT MONTHNAME(o.order_date),
                   SUM(oi.subtotal - (p.cost_price * oi.quantity))
            FROM orders o
            JOIN order_items oi ON o.order_id = oi.order_id
            JOIN products p ON oi.product_id = p.product_id
            GROUP BY MONTH(o.order_date), MONTHNAME(o.order_date)
            ORDER BY MONTH(o.order_date)
        """)
        monthly_profit_raw = cursor.fetchall()

        monthly_profit_labels = [row[0] for row in monthly_profit_raw]
        monthly_profit_values = [float(row[1]) for row in monthly_profit_raw]

        # TOP 5 PRODUCTS CHART
        cursor.execute("""
            SELECT p.product_name, SUM(oi.quantity)
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
            GROUP BY p.product_name
            ORDER BY SUM(oi.quantity) DESC
            LIMIT 5
        """)
        top_products_raw = cursor.fetchall()

        top_product_labels = [
            row[0][:12] + '...' if len(row[0]) > 12 else row[0]
            for row in top_products_raw
]
        top_product_values = [int(row[1]) for row in top_products_raw]

        # CATEGORY SALES CHART
        cursor.execute("""
            SELECT p.category, SUM(oi.subtotal)
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
            GROUP BY p.category
        """)
        category_sales_raw = cursor.fetchall()

        category_labels = [row[0] for row in category_sales_raw]
        category_values = [float(row[1]) for row in category_sales_raw]

    return render(request, 'analytics/report.html', {
        'today_date': str(date.today()),
        'total_revenue': round(total_revenue, 2),
        'total_profit': round(total_profit, 2),
        'total_orders': total_orders,
        'total_customers': total_customers,
        'best_month': best_month,
        'predicted_top_product': predicted_top_product,
        'predicted_top_category': predicted_top_category,
        'predicted_sales': round(predicted_sales, 2),
        'predicted_growth': predicted_growth,

        'monthly_sales_labels': json.dumps(monthly_sales_labels),
        'monthly_sales_values': json.dumps(monthly_sales_values),

        'monthly_profit_labels': json.dumps(monthly_profit_labels),
        'monthly_profit_values': json.dumps(monthly_profit_values),

        'top_product_labels': json.dumps(top_product_labels),
        'top_product_values': json.dumps(top_product_values),

        'category_labels': json.dumps(category_labels),
        'category_values': json.dumps(category_values),
    })


def upload_data_view(request):
    if not check_login(request):
        return redirect('login')

    if request.method == 'POST' and request.FILES.get('file'):
        uploaded_file = request.FILES['file']
        file_name = uploaded_file.name.lower()

        try:
            # Read file
            if file_name.endswith('.csv'):
                df = pd.read_csv(uploaded_file)

            elif file_name.endswith(('.xlsx', '.xls')):
                df = pd.read_excel(uploaded_file)

            elif file_name.endswith('.json'):
                df = pd.read_json(uploaded_file)

            else:
                return render(request, 'analytics/upload_data.html', {
                    'error': 'Only CSV, Excel, and JSON files are allowed.'
                })

            df.columns = df.columns.str.strip()

            # Clear old data
            RetailRaw.objects.all().delete()

            #  BULK INSERT (FIXED POSITION)
            batch = []
            BATCH_SIZE = 5000

            for _, row in df.iterrows():
                try:
                    quantity = int(row['Quantity']) if pd.notnull(row['Quantity']) else 0
                    price = float(row['Price']) if pd.notnull(row['Price']) else 0
                    invoice_date = pd.to_datetime(
                        row['InvoiceDate'],
                        format='%d-%m-%Y %H:%M'
                    )

                    obj = RetailRaw(
                        invoice=str(row['Invoice']).strip(),
                        stock_code=str(row['StockCode']).strip(),
                        description=str(row['Description']).strip(),
                        category=str(row['Category']).strip() if pd.notnull(row['Category']) else 'General',
                        quantity=quantity,
                        invoice_date=invoice_date,
                        price=price,
                        customer_id=int(row['CustomerID']) if pd.notnull(row['CustomerID']) else None,
                        country=str(row['Country']).strip(),
                        revenue=quantity * price,
                        year=invoice_date.year,
                        month=invoice_date.month,
                        day=invoice_date.day,
                        weekday=invoice_date.day_name(),
                        hour=invoice_date.hour
                    )

                    batch.append(obj)

                    if len(batch) == BATCH_SIZE:
                        RetailRaw.objects.bulk_create(batch)
                        batch = []

                except Exception:
                    continue

            if batch:
                RetailRaw.objects.bulk_create(batch)

            # Rebuild tables
            populate_tables()

            return render(request, 'analytics/upload_data.html', {
                'success': 'File uploaded successfully. All pages have been updated.'
            })

        except Exception as e:
            return render(request, 'analytics/upload_data.html', {
                'error': f'Upload failed: {str(e)}'
            })

    return render(request, 'analytics/upload_data.html')


def populate_tables():
    with connection.cursor() as cursor:

        # Clear old table data in correct order
        cursor.execute("DELETE FROM order_items")
        cursor.execute("DELETE FROM payments")
        cursor.execute("DELETE FROM orders")
        cursor.execute("DELETE FROM products")
        cursor.execute("DELETE FROM customers")

        # Customers
        cursor.execute("""
            INSERT IGNORE INTO customers (
                customer_id,
                name,
                email,
                location,
                signup_date
            )
            SELECT 
                customer_id,
                CONCAT('Customer ', customer_id),
                CONCAT('customer', customer_id, '@gmail.com'),
                country,
                MIN(DATE(invoice_date))
            FROM retail_raw
            WHERE customer_id IS NOT NULL
            GROUP BY customer_id, country
        """)

        # Products
        cursor.execute("""
            INSERT INTO products (
                product_id,
                product_name,
                category,
                selling_price,
                cost_price,
                stock_quantity
            )
            SELECT 
                stock_code,
                MIN(description),
                MIN(category),
                MIN(price),
                MIN(price) * 0.7,
                100
            FROM retail_raw
            WHERE stock_code IS NOT NULL
            GROUP BY stock_code
        """)

        # Orders
        cursor.execute("""
            INSERT IGNORE INTO orders (
                order_id,
                customer_id,
                order_date,
                total_amount,
                order_status
            )
            SELECT
                TRIM(invoice),
                customer_id,
                invoice_date,
                SUM(quantity * price),
                CASE
                    WHEN TRIM(invoice) LIKE 'C%%' THEN 'Cancelled'
                    ELSE 'Completed'
                END
            FROM retail_raw
            WHERE customer_id IS NOT NULL
            GROUP BY invoice, customer_id, invoice_date
        """)

        # Order Items
        cursor.execute("""
           INSERT INTO order_items (
                order_id,
                product_id,
                quantity,
                unit_price,
                subtotal
            )
            SELECT 
                rr.invoice,
                rr.stock_code,
                rr.quantity,
                rr.price,
                rr.quantity * rr.price
            FROM retail_raw rr
            INNER JOIN orders o
                ON rr.invoice = o.order_id
        """)

        # Payments
        cursor.execute("""
            INSERT IGNORE INTO payments (
                order_id,
                payment_type,
                payment_value,
                payment_date
            )
            SELECT
                rr.invoice,
                'Card',
                SUM(rr.quantity * rr.price),
                rr.invoice_date
            FROM retail_raw rr
            INNER JOIN orders o
                ON rr.invoice = o.order_id
            GROUP BY rr.invoice, rr.invoice_date
        """)

def full_pdf_report(request):
    if not check_login(request):
        return redirect('login')

    with connection.cursor() as cursor:

        cursor.execute("SELECT IFNULL(SUM(total_amount),0) FROM orders")
        total_revenue = float(cursor.fetchone()[0] or 0)

        cursor.execute("SELECT COUNT(*) FROM orders")
        total_orders = int(cursor.fetchone()[0] or 0)

        cursor.execute("SELECT COUNT(*) FROM customers")
        total_customers = int(cursor.fetchone()[0] or 0)

        cursor.execute("""
            SELECT IFNULL(SUM(oi.subtotal - (p.cost_price * oi.quantity)),0)
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
        """)
        total_profit = float(cursor.fetchone()[0] or 0)

        cursor.execute("""
            SELECT MONTHNAME(order_date), SUM(total_amount) as total_sales
            FROM orders
            GROUP BY MONTH(order_date), MONTHNAME(order_date)
            ORDER BY total_sales DESC
            LIMIT 1
        """)
        best_month_raw = cursor.fetchone()
        best_month = best_month_raw[0] if best_month_raw else "N/A"

        cursor.execute("""
            SELECT p.product_name, SUM(oi.quantity) as total_qty
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
            GROUP BY p.product_name
            ORDER BY total_qty DESC
            LIMIT 1
        """)
        top_product_raw = cursor.fetchone()
        top_product = top_product_raw[0] if top_product_raw else "N/A"

        cursor.execute("""
            SELECT p.category, SUM(oi.subtotal) as total_sales
            FROM order_items oi
            JOIN products p ON oi.product_id = p.product_id
            GROUP BY p.category
            ORDER BY total_sales DESC
            LIMIT 1
        """)
        top_category_raw = cursor.fetchone()
        top_category = top_category_raw[0] if top_category_raw else "N/A"

    response = HttpResponse(content_type='application/pdf')
    response['Content-Disposition'] = 'attachment; filename="RetailIQ_Report.pdf"'

    p = canvas.Canvas(response)

    y = 800

    p.setFont("Helvetica-Bold", 18)
    p.drawString(180, y, "RetailIQ Business Report")

    y -= 40
    p.setFont("Helvetica", 12)
    p.drawString(50, y, f"Date: {date.today()}")

    y -= 40
    p.setFont("Helvetica-Bold", 14)
    p.drawString(50, y, "Business KPI Summary")

    y -= 30
    p.setFont("Helvetica", 12)
    p.drawString(50, y, f"Total Revenue: Rs. {round(total_revenue, 2)}")

    y -= 25
    p.drawString(50, y, f"Total Profit: Rs. {round(total_profit, 2)}")

    y -= 25
    p.drawString(50, y, f"Total Orders: {total_orders}")

    y -= 25
    p.drawString(50, y, f"Total Customers: {total_customers}")

    y -= 40
    p.setFont("Helvetica-Bold", 14)
    p.drawString(50, y, "Business Insights")

    y -= 30
    p.setFont("Helvetica", 12)
    p.drawString(50, y, f"Best Sales Month: {best_month}")

    y -= 25
    p.drawString(50, y, f"Top Product: {top_product}")

    y -= 25
    p.drawString(50, y, f"Top Category: {top_category}")

    y -= 40
    p.setFont("Helvetica-Bold", 14)
    p.drawString(50, y, "Recommendations")

    y -= 30
    p.setFont("Helvetica", 12)
    p.drawString(60, y, "- Promote top-selling products")

    y -= 25
    p.drawString(60, y, "- Give discounts on slow-moving products")

    y -= 25
    p.drawString(60, y, "- Increase stock for high-demand products")

    y -= 25
    p.drawString(60, y, "- Focus more on best-performing category")

    y -= 25
    p.drawString(60, y, "- Reward repeat customers with loyalty offers")

    y -= 50
    p.setFont("Helvetica-Oblique", 10)
    p.drawString(180, y, "RetailIQ Report Generated Successfully")

    p.showPage()
    p.save()

    return response