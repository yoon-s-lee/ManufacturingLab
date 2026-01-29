# 1) Import Equipment data
# https://www.geeksforgeeks.org/python/working-with-excel-files-using-pandas/
# https://www.geeksforgeeks.org/pandas/python-pandas-dataframe/
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

path = r"C:\Users\eyoon\Desktop\ManuLab\Equipment_Cost.xlsx"
df = pd.read_excel(path, sheet_name="Equipment")
equipment_data = df[["Process step", "Equipment", "Ref cost at 5 GWh (M$)", "Scaling exponent"]].copy()
equipment_data["Ref cost at 5 GWh (M$)"] = equipment_data["Ref cost at 5 GWh (M$)"].astype(float)
# equipment_data["Scaling exponent"] = equipment_data["Scaling exponent"].astype(float)
# ^ this gives an error when there is a value that cannot be converted to float
equipment_data["Scaling exponent"]= pd.to_numeric(equipment_data["Scaling exponent"], errors='coerce')

# 2) Ask user for production_capacity_GWh and equipment_life_years
# Input can either be int or float
production_capacity_GWh = float(input("Enter Production capacity (GWh): "))
equipment_life_years = float(input("Enter Equipment Life (years): "))


# 3) scaled_cost_MUSD = ref_cost_MUSD * (production_capacity_GWh/ ref_capacity_GWh)**exponent
# scaling exponent is given for each process step
# ref_capacity_GWh = 5
ref_capacity_GWh = 5
scaled_cost_MUSD = []

for i in range(len(equipment_data) - 1): # -1 to exclude Total row
    ref_cost_MUSD = equipment_data.loc[i, "Ref cost at 5 GWh (M$)"]
    exponent = equipment_data.loc[i, "Scaling exponent"]

    scaled_cost = ref_cost_MUSD * (production_capacity_GWh / ref_capacity_GWh) ** exponent
    scaled_cost_MUSD.append(scaled_cost)


# 4) sum scaled_cost_MUSD -> total_capex_MUSD
total_capex_MUSD = sum(scaled_cost_MUSD)


# 5) annual_depr_USD = (total_capex_MUSD * 1e6) / equipment_life_years
annual_depr_USD = (total_capex_MUSD * 1e6) / equipment_life_years


# 6) depr_per_kWh_USD = annual_depr_USD / (production_capacity_GWh * 1e6)
depr_per_kWh_USD = annual_depr_USD / (production_capacity_GWh * 1e6)


# 7) Write results back to a new Excel file (or add a 'Results' tab).
output = r"C:\Users\eyoon\Desktop\ManuLab\Equipment_Cost_Results.xlsx"
with pd.ExcelWriter(output) as writer:
    results_data = pd.DataFrame({
        "Total Capex (MUSD)": [total_capex_MUSD],
        "Annual Depreciation (USD)": [annual_depr_USD],
        "Depreciation per kWh (USD)": [depr_per_kWh_USD]
    })
    results_data.to_excel(writer, sheet_name="Results", index=False)

    # adding tab, add new page to existing excel file
    # -1 is the last page
    
# 8) Plotting depreciation v. year
n_years = int(np.ceil(equipment_life_years)) 
years = np.arange(1, n_years + 1)
depr_per_year = annual_depr_USD * np.ones(n_years)

plt.figure()
plt.plot(years, depr_per_year)
plt.title("Depreciation v. Year")
plt.xlabel("Year")
plt.ylabel("Depreciation (USD)")
plt.grid()
plt.show()
