
def forecast(monthly_revenues):
    if not monthly_revenues:
        return 0
    return round(sum(monthly_revenues[-3:])/min(3,len(monthly_revenues)),2)
