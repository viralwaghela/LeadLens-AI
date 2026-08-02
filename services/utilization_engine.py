
def therapist_utilization(total_slots, booked_slots):
    if total_slots == 0:
        return 0
    return round((booked_slots/total_slots)*100,2)
