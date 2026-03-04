# SC811 PCF Cost Model


# Given
#   Molar Mass of NMC811 and LMR, molar percentage of each element
#   Cathode specific energy
#   Price of each element compound and utility

# Calculation
#   Material Cost
#   From total cathode mass (production scale * 10^6)/(cathode specific enregy*1000)
#   mass of each element =
#       total cathode mass * molar percentage of cell chemistry
#   mass of each element / molar ratio of compound each element are coming from
#   price of element = price of compound / compound molar ratio
#   element cost = mass of element * price of element

#   Utility Cost
#   utility use = cathode mass * utility intensity
#   utility cost = utility use * utility price

# Output
#   Material Cost = sum of all material
#   Material Cost per kg = material cost / cathode mass
#   Utility Cost = sum of all utility
#   Utility Cost per kg = utility cost / cathode mass
#   Total Cost = Material Cost + Utility Cost
#   Total Cost per kg = Total Cost / cathode mass


#####################################################################################


# 1) Import Data from Excel
# Import
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import os
from pathlib import Path
env_path = os.environ.get("SC811_Cost_Model_edited.xlsx")
if env_path:
    data_path = Path(env_path)
else:
    script_dir = Path(__file__).resolve().parent
    workspace_root = script_dir.parent
    candidates = [
        script_dir / "SC811_Cost_Model_edited.xlsx",
        workspace_root / "SC811_Cost_Model_edited.xlsx",
        Path.cwd() / "SC811_Cost_Model_edited.xlsx",
    ]
    data_path = next((p for p in candidates if p.exists()), None)

if data_path is None or not data_path.exists():
    raise FileNotFoundError(
        "Could not find 'SC811_Cost_Model_edited.xlsx'. Looked in:\n  " +
        "\n  ".join(str(p) for p in candidates) +
        "\nPlace the file in the script directory or workspace root, or set the 'SC811_COST_MODEL_XLSX' environment variable."
    )


# 2) Data
# User Input on 
#   - material choice (NMC-811 or LMR)
#   - Lithium source (LiOH or Li2O)
chemistry_choice = input("NMC-811 or LMR? ").upper()
if chemistry_choice not in ["NMC-811", "LMR"]:
    raise ValueError("Please enter 'NMC-811' or 'LMR'.")

if chemistry_choice == "NMC-811":
    df_cd = pd.read_excel(data_path, sheet_name="PCF", header=1, nrows=2, usecols = "B:M")
elif chemistry_choice == "LMR":
    df_cd = pd.read_excel(data_path, sheet_name="PCF", header=1, nrows=3, usecols = "B:M")
    # drop the first row which is for NMC-811
    df_cd = df_cd.drop(index=0).reset_index(drop=True)

# importing molar percentage and specific energy data
chemistry_data = df_cd[["Cell Chemistry","Stoichiometry","Molar Mass","Lithium","Cobalt","Nickel",
                        "Manganese","Lithium%","Cobalt%","Nickel%","Manganese%","Cathode Specific Energy"]].copy()
chemistry_data["Lithium%"] = pd.to_numeric(chemistry_data["Lithium%"], errors='coerce') 
chemistry_data["Cobalt%"] = pd.to_numeric(chemistry_data["Cobalt%"], errors='coerce') 
chemistry_data["Nickel%"] = pd.to_numeric(chemistry_data["Nickel%"], errors='coerce') 
chemistry_data["Manganese%"] = pd.to_numeric(chemistry_data["Manganese%"], errors='coerce') 
chemistry_data["Cathode Specific Energy"] = pd.to_numeric(chemistry_data["Cathode Specific Energy"], errors='coerce')

lithium_source_choice = input("LiOH or Li2O? ").lower()
if lithium_source_choice not in ["lioh", "li2o"]:
    raise ValueError("Please enter 'LiOH' or 'Li2O'.")

# importing material price
# lithium source
if lithium_source_choice == "lioh":
    lithium_source = pd.read_excel(data_path, sheet_name="PCF", header=5, nrows=2) 
elif lithium_source_choice == "li2o":
    lithium_source = pd.read_excel(data_path, sheet_name="PCF", header=13, nrows=2)
lithium_source["Used per kg Li"] = pd.to_numeric(lithium_source["Used per kg Li"], errors='coerce') 

# nickel source
nickel_source = pd.read_excel(data_path, sheet_name="PCF", header=7, nrows=2)
nickel_source["Used per kg Ni"] = pd.to_numeric(nickel_source["Used per kg Ni"], errors='coerce')

# manganese source
manganese_source = pd.read_excel(data_path, sheet_name="PCF", header=9, nrows=2)
manganese_source["Used per kg Mn"] = pd.to_numeric(manganese_source["Used per kg Mn"], errors='coerce')

# cobalt source
cobalt_source = pd.read_excel(data_path, sheet_name="PCF", header=11, nrows=2)
cobalt_source["Used per kg Co"] = pd.to_numeric(cobalt_source["Used per kg Co"], errors='coerce')

# importing utility price
# water source
water_source = pd.read_excel(data_path, sheet_name="PCF", header=5, nrows=2)
water_source["Used"] = pd.to_numeric(water_source["Used"], errors='coerce')

# electricity source
electricity_source = pd.read_excel(data_path, sheet_name="PCF", header=9, nrows=2)
electricity_source["Used"] = pd.to_numeric(electricity_source["Used"], errors='coerce')

# natural gas source
natural_gas_source = pd.read_excel(data_path, sheet_name="PCF", header=13, nrows=2)
natural_gas_source["Used"] = pd.to_numeric(natural_gas_source["Used"], errors='coerce')


# Cathode Mass= Production Scale (GWh) * 1e6 / (Cathode Specific Energy (Wh/kg) * 1000)
production_scale_GWh = float(input("Production Scale (GWh): "))
cathode_specific_energy = chemistry_data.loc[0, "Cathode Specific Energy"]
cathode_mass_kg = (production_scale_GWh * 1e6) / (cathode_specific_energy * 1000)

# Mass of Each Material
lithium_percentage = chemistry_data.loc[0, "Lithium%"]
cobalt_percentage = chemistry_data.loc[0, "Cobalt%"]    
nickel_percentage = chemistry_data.loc[0, "Nickel%"]
manganese_percentage = chemistry_data.loc[0, "Manganese%"]

lithium_mass_kg = cathode_mass_kg * lithium_percentage
cobalt_mass_kg = cathode_mass_kg * cobalt_percentage
nickel_mass_kg = cathode_mass_kg * nickel_percentage
manganese_mass_kg = cathode_mass_kg * manganese_percentage

# Price per kg Material
lithium_price_per_kg = lithium_source.loc[0, "Used per kg Li"]
nickel_price_per_kg = nickel_source.loc[0, "Used per kg Ni"]
manganese_price_per_kg = manganese_source.loc[0, "Used per kg Mn"]
cobalt_price_per_kg = cobalt_source.loc[0, "Used per kg Co"]

# utility price per material
water_price_per_kg = water_source.loc[0, "Used"]
electricity_price_per_kWh = electricity_source.loc[0, "Used"]
natural_gas_price_per_kWh = natural_gas_source.loc[0, "Used"]



# 3) Calculation
# Mass of each element
total_lithium_mass_kg = cathode_mass_kg * lithium_percentage
total_cobalt_mass_kg = cathode_mass_kg * cobalt_percentage
total_nickel_mass_kg = cathode_mass_kg * nickel_percentage
total_manganese_mass_kg = cathode_mass_kg * manganese_percentage

total_lithium_cost = total_lithium_mass_kg * lithium_price_per_kg
total_cobalt_cost = total_cobalt_mass_kg * cobalt_price_per_kg
total_nickel_cost = total_nickel_mass_kg * nickel_price_per_kg
total_manganese_cost = total_manganese_mass_kg * manganese_price_per_kg

total_material_cost = total_lithium_cost + total_cobalt_cost + total_nickel_cost + total_manganese_cost
total_material_cost_per_kg = total_material_cost / cathode_mass_kg

# Utility Cost 
electricity_usage_factor = float(input("Electricity Intensity: "))
water_usuage_factor = float(input("Water Intensity: "))
natural_gas_usage_factor = float(input("Natural gas Intensity: "))

total_electricity_cost = cathode_mass_kg * electricity_usage_factor * electricity_price_per_kWh
total_natural_gas_cost = cathode_mass_kg * natural_gas_usage_factor * natural_gas_price_per_kWh
total_water_cost = cathode_mass_kg * water_usuage_factor * water_price_per_kg

total_utility_cost = total_electricity_cost + total_natural_gas_cost + total_water_cost
total_utility_cost_per_kg = total_utility_cost / cathode_mass_kg

total_cost = total_material_cost + total_utility_cost
total_cost_per_kg = total_cost / cathode_mass_kg

print(chemistry_choice + " - " + lithium_source_choice)
print("Total Material Cost: " + str(total_material_cost))
print("Total Material Cost per kg: " + str(total_material_cost_per_kg))
print("Total Utility Cost: " + str(total_utility_cost))
print("Total Utility Cost per kg: " + str(total_utility_cost_per_kg))
print("Total Cost: " + str(total_cost))
print("Total Cost per kg: " + str(total_cost_per_kg))











