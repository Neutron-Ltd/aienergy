# Unified Energy Management System (EMS) Database Schema

## Project Overview
This database, named **`unified`**, serves as the backend for an Industrial Energy Management System. It is designed to handle high-frequency telemetry from smart meters, manage device configurations, and calculate complex billing based on regional pricing rules (specifically the local region, China).

The architecture utilizes a **Sharding Strategy** for time-series data:
* **Static Configuration:** Tables like `meter_info` and `pricing` store permanent settings.
* **Daily Log Shards:** Tables like `site_power_250725` are created daily to store massive volumes of raw sensor data.

---

## 1. Core Configuration Tables
These tables define the physical infrastructure and billing rules.

### `meter_info` (Device Registry)
The central inventory of all smart meters in the network.
* **`id`** *(Primary Key)*: Unique system identifier for the meter.
* **`meter_name`**: Human-readable label (e.g., "Factory 1 Main Incomer").
* **`mac_address`**: Physical address of the communication module.
* **`meter_number`**: Unique logical number used to link data logs to this device.
* **`type_code`**: Links to `meter_type` to define hardware model/protocol.
* **`ratio`**: **CT/PT Multiplier**. Critical for calculation.
    * *Usage:* `Real Value` = `Raw Reading` * `ratio`.
    * *Example:* A ratio of `200` converts a 5A raw signal into a 1000A real reading.

### `meter_type`
Lookup table defining supported hardware models.
* **`type_code`**: ID referenced by `meter_info`.
* **`type_name`**: The specific model name or protocol standard.

### `electricity_pricing`
Hardcoded pricing rules for the local region, enabling "Shadow Billing" verification.
* **`user_category`**: Customer type (e.g., "Large Industrial", "Commercial").
* **`voltage_level`**: Service voltage (e.g., "1-10kV", "35kV").
* **`peak_period_price`**: Rate during busiest grid hours (Highest cost).
* **`valley_period_price`**: Rate during night/low-demand (Lower cost).
* **`deep_valley_price`**: Discounted rate (Super-off-peak).
* **`max_demand_charge`**: Fee based on the highest peak power (kW) hit during the month.

---

## 2. Telemetry Tables (Time-Series Data)
**Note:** These tables are generated dynamically every day. The suffix `_YYMMDD` represents the date (e.g., `_250725` = July 25, 2025).

### `site_power_XXXXXX` (Power Quality / "Speedometer")
Stores instantaneous snapshots of electrical health (Volts, Amps, Watts).

| Column | Unit | Symbol | Description |
| :--- | :--- | :--- | :--- |
| **`ts`** | Time | - | Timestamp of the reading. |
| **`Uan`, `Ubn`, `Ucn`** | V | $V_{ph}$ | **Phase Voltage** (Line-to-Neutral). |
| **`Uab`, `Ubc`, `Uca`** | V | $V_{LL}$ | **Line Voltage** (Phase-to-Phase). |
| **`Ia`, `Ib`, `Ic`** | A | $I$ | **Current** per phase. Imbalance here damages motors. |
| **`Pt`** | kW | $P_{total}$ | **Total Active Power**. The real work being done. |
| **`Qt`** | kvar | $Q_{total}$ | **Total Reactive Power**. Magnetic "wasted" power. |
| **`St`** | kVA | $S_{total}$ | **Apparent Power**. Total grid capacity used. |
| **`FPt`** | - | $PF$ | **Power Factor**. Efficiency score (0.0 - 1.0). <br>If `FPt < 0.90`, penalties apply. |
| **`freq`** | Hz | $f$ | Grid Frequency (Standard: 50.00Hz). |

### `site_energy_XXXXXX` (Consumption / "Odometer")
Stores cumulative energy counters used for billing.

| Column | Unit | Description |
| :--- | :--- | :--- |
| **`TotalEP`** | kWh | **Total Active Energy**. The main billing counter. |
| **`ImpEP`** | kWh | **Import Energy**. Electricity bought from the grid. |
| **`ExpEP`** | kWh | **Export Energy**. Electricity sent back to the grid (e.g., Solar). |
| **`Q1Eq`...`Q4Eq`** | kvarh | **4-Quadrant Reactive Energy**. Used to analyze load types (inductive vs capacitive). |

---

## 3. Financial Logic Tables

### `power_factor_penalty`
Defines fines for poor efficiency.
* **`power_factor`**: Threshold (e.g., `0.85`).
* **`penalty_percentage`**: Multiplier added to the bill if PF is below this threshold.

### `power_factor_reduction`
Defines rewards for high efficiency.
* **`power_factor`**: Threshold (e.g., `0.95`).
* **`reduction_percentage`**: Discount subtracted from the bill.

---

## 4. User & Session Management
Standard web application tables for the dashboard login.

* **`site_users`**: Stores login credentials (`username`, `password_hash`), roles (`admin`, `regular`), and profile info.
* **`site_sessions`**: Tracks active user sessions, `expires_at`, and `ip_address` for security auditing.

---

## 5. Sample Queries

### Check Latest Device Status
```sql
SELECT 
    m.meter_name, 
    p.Uan, p.Ubn, p.Ucn, -- Voltages
    p.Pt,                -- Current Load (kW)
    p.FPt                -- Efficiency (Power Factor)
FROM meter_info m
JOIN site_power_250725 p ON m.meter_number = p.meter_number
ORDER BY p.ts DESC
LIMIT 10;