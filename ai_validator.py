import os
import logging
from dotenv import load_dotenv

logger = logging.getLogger("ai_validator")

# Try importing google.generativeai but don't crash if it isn't available
try:
    import google.generativeai as genai
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
        genai.configure(api_key=api_key)
        # Using gemini-2.5-flash for the latest advanced reasoning and speed
        model = genai.GenerativeModel("gemini-2.5-flash")

        regime = trade_context.get("regime", "UNKNOWN")
        spot_price = trade_context.get("spot_price", 0)
        strategy = trade_context.get("suggested_strategy", "UNKNOWN")
        net_credit = trade_context.get("net_credit_expected", 0)
        atr_3d_avg = trade_context.get("atr_3d_avg", 0)
        current_atr = trade_context.get("atr_at_entry", 0)
        trend_4h = trade_context.get("trend_4h_movement", 0)
        funding_rate = trade_context.get("funding_rate", 0)
        
        # Prepare the exact context payload
        prompt = f"""
You are a highly conservative quantitative options trading assistant.
Your job is to provide a final sanity check on a 0-DTE crypto options trade proposed by a purely mathematical algorithm.

Here is the current market context:
- Spot Price: ${spot_price}
- Market Regime Detected: {regime}
- 1-Hour ATR: {current_atr:.2f} (Average is {atr_3d_avg:.2f})
- 4-Hour Trend Momentum: ${trend_4h:.2f}
- Funding Rate (Sentiment): {funding_rate}
- The proposed Strategy is: {strategy}
- Expected Net Credit per lot: ${net_credit:.2f}

Based on these quantitative indicators and general macroeconomic context for crypto, write a brief, 2-sentence risk assessment.
Then, on a new line, write exactly: "CONFIDENCE: X/10", where X is a score from 1 to 10. 
If the trade feels unusually risky, counter-trend, or if volatility is mysteriously spiking, score it below 5. If it aligns perfectly with the strategy rules, score it 7 or higher.
"""
        
        response = model.generate_content(prompt)
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
        logger.error(f"AI Validation API Call Failed: {e}")
        return {
            "confidence_score": 10,
            "rationale": f"API request error: {str(e)}. Proceeding safely purely on math."
        }
