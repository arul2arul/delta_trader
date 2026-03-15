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

        prompt = f"""You are a highly conservative quantitative options trading assistant.
Your job is to provide a final sanity check on a 0-DTE crypto options trade proposed by a purely mathematical algorithm.

Here is the current market context:
- Spot Price: ${spot_price}
- Market Regime Detected: {regime}
- 1-Hour ATR: {current_atr:.2f} (Average is {atr_3d_avg:.2f})
- 4-Hour Trend Momentum: ${trend_4h:.2f}
- Funding Rate (Sentiment): {funding_rate} (Positive = Bullish sentiment/Long leverage, Negative = Bearish sentiment/Short leverage)
- Order Book Imbalance: {ob_imbalance:.2f} (-1.0 to 1.0, where -1.0 is extreme sell pressure and 1.0 is extreme buy pressure)
- The proposed Strategy is: {strategy}
- Expected Net Credit per lot: ${net_credit:.2f}

CRITICAL ASSESSMENT RULES:
1. SQUEEZE RISK: If the funding rate is extremely negative and price is moving up, or vice versa, warn of a potential "Short Squeeze" or "Long Squeeze".
2. STOP LOSS CLUSTERS: Look at the Order Book Imbalance. If a "Sideways" strategy is proposed but imbalance is heavily skewed (>0.4 or <-0.4), warn that "Stop Loss Clusters" are likely being targeted, making the trade risky.
3. LIQUIDITY: High imbalance combined with high ATR suggests a breakout is imminent. Block "Sideways" strategies in these cases.

Write a brief, 2-sentence risk assessment based on these deep liquidity signals.
Then, on a new line, write exactly: "CONFIDENCE: X/10", where X is a score from 1 to 10.
Score below 5 if there is a squeeze risk or targeted stop clusters.
"""
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        text = response.text.strip()
        
        # Parse the confidence score out of the text
        confidence = 10  # Default fallback
        rationale = text
        
        for line in text.split('\n'):
            if "CONFIDENCE:" in line.upper():
                try:
                    # Extract "8/10" -> "8" -> 8
                    score_str = line.upper().split("CONFIDENCE:")[1].strip().split("/")[0]
                    confidence = int(score_str)
                except Exception as e:
                    logger.error(f"Failed to parse confidence score from AI: {e}")
                    
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
