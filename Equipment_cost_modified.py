#!/usr/bin/env python
# coding: utf-8

# In[17]:


# 1) Import Equipment data
# https://www.geeksforgeeks.org/python/working-with-excel-files-using-pandas/
# https://www.geeksforgeeks.org/pandas/python-pandas-dataframe/
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

path = r"C:\Users\eyoon\Desktop\ManuLab\Equipment_Cost.xlsx"
df = pd.read_excel(path, sheet_name="Equipment")
equipment_data = df[["Process step", "Equipment", "Ref cost at 5 GWh (M$)", "Scaling exponent"]].copy()
# astype(float): gives an error when there is a value that cannot be converted to float
equipment_data["Ref cost at 5 GWh (M$)"] = pd.to_numeric(equipment_data["Ref cost at 5 GWh (M$)"], errors='coerce')
equipment_data["Scaling exponent"]= pd.to_numeric(equipment_data["Scaling exponent"], errors='coerce')

# implement capex scaling functione w.o. modifying original dataframe
def equipment_capex_MUSD( 
    equipment_data: pd.DataFrame, 
    S_target_GWh: float, 
    S_ref_GWh: float = 5.0, 
) -> tuple[pd.DataFrame, float]: 
    """ 
    Given equipment_data with columns: 
      - 'Process step' 
      - 'Ref cost at 5 GWh (M$)' 
      - 'Scaling exponent' 
    and a target capacity S_target_GWh (GWh/year), 
    return: 
      - a DataFrame with an added column 'Capex_MUSD' containing 
        per-step capex at S_target_GWh 
      - total capex at S_target_GWh in MUSD (float) 
    """ 
    df2 = equipment_data.copy()
    
    # C_k = C_{k.ref} * (S/S_ref)^{b_k}
    df2["Capex_MUSD"] = (df2["Ref cost at 5 GWh (M$)"]*(S_target_GWh/ S_ref_GWh)**df2["Scaling exponent"])
    total_capex_MUSD = df2["Capex_MUSD"].sum()
    return df2, float(total_capex_MUSD)

# 2) Ask user for production_capacity_GWh and equipment_life_years (Part C)
# Input can either be int or float
production_capacity_GWh = float(input("Enter Production capacity (GWh): "))
equipment_life_years = float(input("Enter Equipment Life (years): "))

"""
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
"""

# 3) total_capex_MUSD is sum of Capex_MUSD across all steps
ref_capacity_GWh = 5
equipment_data_except_total = equipment_data.iloc[:-1].copy()
equipment_data_capex, total_capex_MUSD = equipment_capex_MUSD(
    equipment_data = equipment_data_except_total,
    S_target_GWh = production_capacity_GWh,
    S_ref_GWh = ref_capacity_GWh)

# Part B)
capacities = [5, 3.6, 14, 56, 200]
total_row = []

for x in capacities:
    _, total_capex_MUSD = equipment_capex_MUSD(
    equipment_data = equipment_data_except_total,
    S_target_GWh = x,
    S_ref_GWh = 5)
    
    total_row.append({
        "Capacity_GWh": x, # need for plot
        "Total_capex_MUSD": total_capex_MUSD,
        "Total_capex_BUSD": total_capex_MUSD/1000
    })
    
total_df = pd.DataFrame(total_row)
print(total_df)

# 5) annual_depr_USD = (total_capex_MUSD * 1e6) / equipment_life_years (Part C)
annual_depr_USD = (total_capex_MUSD * 1e6) / equipment_life_years
print (annual_depr_USD)

# 6) depr_per_kWh_USD = annual_depr_USD / (production_capacity_GWh * 1e6)
depr_per_kWh_USD = annual_depr_USD / (production_capacity_GWh * 1e6)


# In[18]:


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
"""   
# 8) Plotting depreciation v. year
n_years = int(equipment_life_years)
years = np.arange(1, n_years + 1)
depr_per_year = annual_depr_USD * np.ones(n_years)

plt.figure()
plt.plot(years, depr_per_year)
plt.title("Depreciation v. Year")
plt.xlabel("Year")
plt.ylabel("Depreciation (USD)")
plt.grid()
plt.show()
"""

# Part D) using Part B, plot Capacity_GWh v. Total_capex_BUSD
plt.figure()
plt.plot(total_df["Capacity_GWh"], total_df["Total_capex_BUSD"])
plt.xlabel("Capacity")
plt.ylabel("Total Capex (BUSD)")
plt.title("Capacity (GWh) v. Total Equipment Capex")
plt.grid()
plt.show()


# # 9) Plotting Depreciation v. Year
# # start from Total Capex USD (convert MUSD -> USD)
# # subtracting annual_depr_USD (rate) * years so that curve shows the decreasing as the time pass
# 
# n_years = int(equipment_life_years)
# years = np.arange(0, n_years + 1) #start from 0 when the left = total
# 
# total_capex_USD = total_capex_MUSD * 1e6
# left_USD = total_capex_USD - annual_depr_USD * years
# 
# plt.figure()
# plt.plot(years, left_USD)
# plt.title("Depreciation v. Year")
# plt.xlabel("Year")
# plt.ylabel("Value (USD)")
# plt.grid()
# plt.show()

# In[ ]:




