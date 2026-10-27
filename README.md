# Dtrader

A high-performance automated Deriv CLI trading bot built for Python and optimized for Termux. It executes a momentum-based mean-reversion strategy using the Relative Strength Index (RSI) across multiple synthetic and derived indices concurrently.

## Strategy Overview: RSI 30 / RSI 60
* **Technical Indicator:** RSI period of 7, computed from server-provided 1-minute historical candles.
* **Entry Trigger (Buy):** Automatically dispatches a `MULTUP` buy order when an asset's RSI drops to **$\le 30$** (oversold condition).
* **Exit Target (Take-Profit):** Locks in gains using server-verified limit orders as the price recovers toward the **RSI 60** threshold.
* **Risk Management:** Dynamic stake and take-profit scaling based on live account balance tiers maintaining a strict **1:1 Risk-to-Reward ratio**.
* **Parallel Multi-Symbol Scanning:** Continuously scans 15+ high-frequency markets simultaneously (including Volatility indices, Jump indices, and Step Index) to minimize idle time.
* **Smart Session Closing:** Runs continuously until a strict performance milestone is achieved—shutting down automatically only when **$\ge 15$ trades** are completed with an overall win rate of **$\ge 80\%$**.

---

## Features
* **Termux & Linux Ready:** Built-in automatic dependency resolution for WebSocket and cryptography packages.
* **Server-Verified Tracking:** Real-time WebSocket connection to Deriv API with TLS certificate pinning and secure session authentication.
* **Fresh Session Stats:** Clears outdated historical records on startup to calculate precise win/loss ratios for the current run.
* **Security Watchdog:** Integrated memory and trace process checks for safe execution.

---

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/kipkorirchirchir800-hash/Dtrader.git](https://github.com/kipkorirchirchir800-hash/Dtrader.git)
   cd Dtrader
