# ==========================================
# 📊 WEATHER ANALYSIS + ML PROJECT (FINAL - 1 YEAR DATA)
# ==========================================

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

from sklearn.model_selection import train_test_split
from sklearn.linear_model import LinearRegression
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.naive_bayes import GaussianNB
from sklearn.metrics import mean_squared_error, accuracy_score, r2_score
from sklearn.preprocessing import StandardScaler

from scipy import stats

# =========================
# 1. DATASET (VELLORE - 1 YEAR AUTO GENERATED)
# =========================

np.random.seed(42)

days = 365
dates = pd.date_range(start="2024-01-01", periods=days)

# 🌡️ Temperature
temperature = 30 + 5*np.sin(2*np.pi*dates.dayofyear/365) + np.random.normal(0,1.5,days)

# 💧 Humidity
humidity = 60 - 10*np.sin(2*np.pi*dates.dayofyear/365) + np.random.normal(0,5,days)

# 🌫️ Pressure
pressure = 1010 + np.random.normal(0,3,days)

# 🌬️ Wind Speed
wind_speed = np.random.randint(3, 10, days)

# 🌧️ Rain (IMPROVED LOGIC 🔥)
rain = []
for i in range(days):
    h = humidity[i]
    t = temperature[i]
    w = wind_speed[i]

    prob = 0.1

    if h > 75:
        prob += 0.5
    elif h > 65:
        prob += 0.3
    elif h > 55:
        prob += 0.15

    if 25 <= t <= 32:
        prob += 0.1

    if w > 8:
        prob += 0.05

    rain.append(1 if np.random.rand() < prob else 0)

rain = np.array(rain)

data = pd.DataFrame({
    "date": dates,
    "temperature": temperature,
    "humidity": np.clip(humidity,30,90),
    "pressure": pressure,
    "wind_speed": wind_speed,
    "rain": rain
})

# Convert rain to binary
data['rain'] = data['rain'].apply(lambda x: 1 if x > 0 else 0)

print("\nDataset Preview:\n", data.head())

# =========================
# 2. FEATURE ENGINEERING
# =========================

data['temp_lag1'] = data['temperature'].shift(1)
data['temp_rolling_mean'] = data['temperature'].rolling(3).mean()

data = data.dropna()

# =========================
# 3. FEATURES & TARGET
# =========================

X = data[['humidity', 'pressure', 'wind_speed', 'temp_lag1', 'temp_rolling_mean']]
y_temp = data['temperature']
y_rain = data['rain']

# Scaling
scaler = StandardScaler()
X_scaled = scaler.fit_transform(X)

# Train-test split
X_train, X_test, y_train_temp, y_test_temp, y_train_rain, y_test_rain = train_test_split(
    X_scaled, y_temp, y_rain, test_size=0.2, random_state=42
)

# =========================
# 4. EDA (HEATMAP)
# =========================

plt.figure()
sns.heatmap(data.corr(), annot=True, cmap="coolwarm")
plt.title("Correlation Heatmap")
plt.show()

# =========================
# 5. MODELS
# =========================

lr = LinearRegression()
lr.fit(X_train, y_train_temp)
lr_pred = lr.predict(X_test)

rf = RandomForestRegressor(n_estimators=200, random_state=42)
rf.fit(X_train, y_train_temp)
rf_pred = rf.predict(X_test)

gb = GradientBoostingRegressor()
gb.fit(X_train, y_train_temp)
gb_pred = gb.predict(X_test)

nb = GaussianNB()
nb.fit(X_train, y_train_rain)
rain_pred = nb.predict(X_test)

# =========================
# 6. ENSEMBLE MODEL
# =========================

final_pred = 0.4*rf_pred + 0.3*gb_pred + 0.3*lr_pred

# =========================
# 7. EVALUATION
# =========================

def evaluate(y_true, pred, name):
    rmse = np.sqrt(mean_squared_error(y_true, pred))
    r2 = r2_score(y_true, pred)
    print(f"{name} -> RMSE: {rmse:.2f}, R2: {r2:.2f}")

print("\n--- MODEL PERFORMANCE ---")
evaluate(y_test_temp, lr_pred, "Linear Regression")
evaluate(y_test_temp, rf_pred, "Random Forest")
evaluate(y_test_temp, gb_pred, "Gradient Boosting")
evaluate(y_test_temp, final_pred, "Final Ensemble")

print("\nRain Accuracy:", accuracy_score(y_test_rain, rain_pred))

# =========================
# 8. GRAPH
# =========================

plt.figure()
plt.plot(y_test_temp.values, label="Actual")
plt.plot(final_pred, linestyle="dashed", label="Predicted")
plt.legend()
plt.title("Actual vs Predicted Temperature")
plt.show()

# =========================
# 9. STATISTICS
# =========================

print("\n--- STATISTICS ---")
print("Mean Temp:", np.mean(y_temp))
print("Median Temp:", np.median(y_temp))
print("Std Dev:", np.std(y_temp))

P_rain = np.mean(y_rain)
print("Probability of Rain:", P_rain)

t_stat, p_val = stats.ttest_1samp(y_temp, 32)
print("t-stat:", t_stat, "p-value:", p_val)

# =========================
# 10. USER INPUT
# =========================

print("\n--- USER INPUT PREDICTION ---")

humidity_in = float(input("Enter Humidity (%): "))
pressure_in = float(input("Enter Pressure (hPa) (Between 870 to 1085): "))
wind_in = float(input("Enter Wind Speed (km/h): "))
lag_temp_in = float(input("Enter Previous Day Temp (°C): "))

user_data = pd.DataFrame({
    'humidity': [humidity_in],
    'pressure': [pressure_in],
    'wind_speed': [wind_in],
    'temp_lag1': [lag_temp_in],
    'temp_rolling_mean': [lag_temp_in]
})

user_scaled = scaler.transform(user_data)

lr_u = lr.predict(user_scaled)
rf_u = rf.predict(user_scaled)
gb_u = gb.predict(user_scaled)

final_u = 0.4*rf_u + 0.3*gb_u + 0.3*lr_u
rain_u = nb.predict(user_scaled)

rain_prob = nb.predict_proba(user_scaled)[0][1]

# =========================
# 11. EXTRA INSIGHTS
# =========================

if final_u[0] >= 36:
    category = "Hot"
elif final_u[0] >= 30:
    category = "Moderate"
else:
    category = "Cool"

feels_like = final_u[0] + 0.1*(humidity_in-50)
trend = final_u[0] - lag_temp_in

# =========================
# 12. HUMAN-FRIENDLY OUTPUT 
# =========================

print("\n================ WEATHER ANALYSIS RESULT ================\n")

print(f"Predicted Temperature: {round(final_u[0],2)} °C")

if final_u[0] >= 36:
    print("It will feel quite HOT today")
elif final_u[0] >= 30:
    print("Weather will be MODERATE")
else:
    print("Weather will be COOL")

print(f"\nChance of Rain: {round(rain_prob*100,2)} %")

if rain_prob > 0.6:
    print("High chance of rain")
elif rain_prob > 0.3:
    print("Moderate chance of rain")
else:
    print("Very low chance of rain")

print("\nModel Predictions:")
print(f"- Linear Regression: {round(lr_u[0],2)} °C")
print(f"- Random Forest: {round(rf_u[0],2)} °C")
print(f"- Gradient Boosting: {round(gb_u[0],2)} °C")
print("Final prediction is combined (more accurate)")

print(f"\nFeels Like: {round(feels_like,2)} °C")

if feels_like > final_u[0]:
    print("Feels hotter due to humidity")

print("\nTemperature Trend:")
if trend > 1:
    print("Increasing")
elif trend < -1:
    print("Decreasing")
else:
    print("Stable")

print("\nHumidity Condition:")
if humidity_in > 65:
    print("Humid")
elif humidity_in > 40:
    print("Comfortable")
else:
    print("Dry")

print("\nWind Condition:")
if wind_in > 8:
    print("Windy")
elif wind_in < 4:
    print("Calm")
else:
    print("Breezy")

print("\n================ SUMMARY ================\n")
print(f"Temperature: {round(final_u[0],2)} °C")
print(f"Feels Like: {round(feels_like,2)} °C")
print(f"Rain Probability: {round(rain_prob*100,2)} %")
print(f"Condition: {category}")

# =========================
# 13. FUTURE FORECAST
# =========================

print("\n--- NEXT 3 DAYS FORECAST ---")

temp_next = final_u[0]
for i in range(1,4):
    temp_next = temp_next + np.random.uniform(-1,1)
    print(f"Day {i}: {round(temp_next,2)} °C")