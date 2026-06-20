def estimate_gguf_size(model_id: str, filename: str) -> int:
    """
    Estimate GGUF file size based on model parameters and quantization.
    Returns size in bytes, or 0 if estimation fails.
    """
    # Quantization bytes per parameter (approximate)
    quant_patterns = {
        'Q2_K': 0.35, 'Q2_K_S': 0.30, 'Q2_K_M': 0.33,
        'Q3_K_S': 0.42, 'Q3_K_M': 0.47, 'Q3_K_L': 0.50,
        'Q4_0': 0.50, 'Q4_K_S': 0.50, 'Q4_K_M': 0.55, 'Q4_K_L': 0.60,
        'Q5_0': 0.65, 'Q5_K_S': 0.65, 'Q5_K_M': 0.70, 'Q5_K_L': 0.75,
        'Q6_K': 0.80, 'Q8_0': 1.0, 'Q8_K': 1.0,
        'F16': 2.0, 'BF16': 2.0, 'F32': 4.0,
    }

    # Known model family sizes (in billions of parameters)
    known_models = {
        'phi-3-mini': 3.8, 'phi-3.5-mini': 3.8, 'phi-4': 14,  # phi-4 ~14B
        'llama-2-7b': 7, 'llama-2-13b': 13, 'llama-2-70b': 70,
        'llama-3-8b': 8, 'llama-3-70b': 70,
        'llama-3.1-8b': 8, 'llama-3.1-70b': 70, 'llama-3.1-405b': 405,
        'mistral-7b': 7, 'mixtral-8x7b': 47,  # Mixtral effective ~47B
        'gemma-2b': 2, 'gemma-7b': 7,
    }

    # Determine bytes per parameter from quantization
    bpc = 2.0  # Default F16
    filename_lower = filename.lower()
    for quant, bytes_per_param in quant_patterns.items():
        if quant.lower() in filename_lower:
            bpc = bytes_per_param
            break

    # Try to extract parameter count from model_id or filename
    import re
    param_count = None

    # Look for explicit number followed by B (7B, 13B, 70B, etc.)
    match = re.search(r'(\d+(?:\.\d+)?)\s*[bB]', model_id + ' ' + filename)
    if match:
        try:
            param_count = float(match.group(1))
        except:
            pass

    # If not found, check known model families
    if param_count is None:
        model_id_lower = model_id.lower()
        for family, params in known_models.items():
            if family in model_id_lower:
                param_count = params
                break

    if param_count is None:
        return 0

    total_params = param_count * 1_000_000_000
    estimated_size = int(total_params * bpc)
    return estimated_size
