import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger("ai_validator")

# Try importing google.genai (new SDK) but don't crash if it isn't available
try:
    from google import genai
    from google.genai import types
    AI_AVAILABLE = True
except ImportError:
    AI_AVAILABLE = False


def ask_ai_for_second_opinion(trade_context: dict) -> dict:
    """
    Sends the quantitative trade context to Gemini for a qualitative second opinion.
    Returns a dict with 'confidence_score' (1-10) and 'rationale' (str).
    """
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")

    if not AI_AVAILABLE or not api_key:
        logger.warning("AI Validation skipped: google-generativeai not installed or GEMINI_API_KEY missing.")
        return {
            "confidence_score": 10,  # Auto-approve if AI isn't configured
            "rationale": "AI Validation disabled or missing API Key. Proceeding purely on mathematical indicators."
        }

    try:
        client = genai.Client(api_key=api_key)

        regime      = trade_context.get("regime", "UNKNOWN")
        spot_price  = trade_context.get("spot_price", 0)
        strategy    = trade_context.get("suggested_strategy", "UNKNOWN")
        net_credit  = trade_context.get("net_credit_expected", 0)
        atr_3d_avg  = trade_context.get("atr_3d_avg", 0)
        current_atr = trade_context.get("atr_at_entry", 0)
        trend_4h    = trade_context.get("trend_4h_movement", 0)
        funding_rate = trade_context.get("funding_rate", 0)
        ob_imbalance = trade_context.get("ob_imbalance", 0)
        ob_depth     = trade_context.get("ob_depth", {})
        basket       = trade_context.get("recommended_orders", [])
        hours_to_exp = trade_context.get("hours_to_expiry", 24)
        lot_size     = trade_context.get("final_lots", 0)

        prompt = f"""[ROLE] You are an expert Crypto Options Risk Controller. Your goal is to prevent the execution of trades where liquidity is poor or slippage destroys the expected Value at Risk (VaR).

[TASK] Analyze the provided "Basket Order" context and decide if the execution is "Safe" for a {lot_size}-lot entry ({lot_size/1000:.3f} BTC equivalent).

[MARKET CONTEXT]
- Spot Price: ${spot_price}
- Market Regime: {regime}
- 1H ATR: {current_atr:.2f} (Avg: {atr_3d_avg:.2f})
- 4H Momentum: ${trend_4h:.2f}
- Funding Rate: {funding_rate}
- OB Imbalance: {ob_imbalance:.2f}
- Hours to Expiry: {hours_to_exp:.2f}

[L2 ORDER BOOK DEPTH (Top 5)]
- Bids: {ob_depth.get('bids', [])}
- Asks: {ob_depth.get('asks', [])}

[PROPOSED BASKET]
{basket}

[ANALYSIS CRITERIA]
1. SLIPPAGE CHECK: Analyze the OB Depth. Detect if the entry of {lot_size} lots will cause > 5% spread impact. If depth within 2 ticks is < 5,000 lots, flag as HIGH RISK.
2. GAMMA RISK: If Hours to Expiry < 2.0 AND Spot Price is within 0.5% of any "Short" strike in the basket, set confidence to 0.
3. ORDER BOOK BIAS: Look for "Liquidation Walls". If there is a massive volume cluster (sell-wall) just above a Bull Put Spread or (buy-wall) below a Bear Call Spread, reject the trade.
4. REGIME MISMATCH: If Regime suggests a "Breakout" (High ATR/Momentum) but the basket is a "Sideways" Iron Condor, set confidence to 3.

[OUTPUT FORMAT]
You MUST respond ONLY with a raw JSON object string:
{{
  "confidence": X,
  "rationale": "Brief explanation of risk assessment",
  "is_safe": true/false
}}
Where X is 1-10. Score below 6 = is_safe: false.
"""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = response.text.strip()
        
        # Parse JSON response
        import json
        import re

        confidence = 10
        rationale = text
        
        # Look for JSON in the response (robust to AI preamble)
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                confidence = data.get("confidence", confidence)
                rationale = data.get("rationale", rationale)
                is_safe = data.get("is_safe", True)
                if not is_safe and confidence >= 6:
                    confidence = 5 # Force fail if AI says not safe
            except Exception as e:
                logger.error(f"Failed to parse JSON from AI: {e}")
        else:
            # Fallback for non-JSON response
            for line in text.split('\n'):
                if "CONFIDENCE:" in line.upper():
                    try:
                        score_str = line.upper().split("CONFIDENCE:")[1].strip().split("/")[0]
                        confidence = int(score_str)
                    except: pass
                    
        return {
            "confidence_score": confidence,
            "rationale": rationale
        }

    except Exception as e:
        error_str = str(e)
        # Detect quota/token exhaustion specifically
        if any(k in error_str.lower() for k in ["quota", "resource_exhausted", "429", "rate limit", "token"]):
            logger.warning(
                f"AI Validation SKIPPED: Gemini API quota/token exhausted. "
                f"Trade will proceed on math alone. Error: {error_str[:200]}"
            )
            return {
                "confidence_score": 10,
                "rationale": (
                    f"⚠️ Gemini API quota exhausted (429 / ResourceExhausted). "
                    f"AI check skipped. Trade approved purely on quantitative math criteria."
                )
            }
        else:
            logger.warning(
                f"AI Validation SKIPPED: Unexpected API error. "
                f"Trade will proceed on math alone. Error: {error_str[:200]}"
            )
            return {
                "confidence_score": 10,
                "rationale": (
                    f"⚠️ Gemini API error: {error_str[:120]}. "
                    f"AI check skipped. Trade approved purely on quantitative math criteria."
                )
            }
