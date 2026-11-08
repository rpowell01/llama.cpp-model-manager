# Llama.cpp Model Manager

## Overview

The Llama.cpp Model Manager is a comprehensive web-based interface for managing and optimizing llama.cpp model deployments. This project provides an intuitive web dashboard for monitoring, configuring, and analyzing the performance of llama.cpp models, with a focus on empirical optimization and real-time performance monitoring.

## Key Features

- **Web-based Dashboard**: Intuitive interface for model management and monitoring
- **Empirical Performance Analysis**: Automatic collection and analysis of model metrics
- **Optimal Configuration Recommendations**: Data-driven suggestions for model settings
- **Real-time Monitoring**: Live performance graphs and metrics
- **Model Configuration Management**: Easy management of model configurations via JSON files
- **Multi-Model Support**: Simultaneous management of multiple model instances

## Architecture

The project consists of several core components:

- **Frontend**: Web interface built with HTML, CSS, and JavaScript
- **Backend**: Model management and analysis logic
- **Data Storage**: Model configuration files (models.json) and performance metrics
- **Performance Analysis Engine**: System for collecting and analyzing model metrics

The architecture follows a clean separation of concerns, with the web interface handling user interaction, the backend managing model operations, and the performance analysis engine providing data-driven insights.

## Getting Started

### Prerequisites

- Node.js (v16 or higher)
- npm (Node Package Manager)
- Python 3.8+ (for some model operations)
- llama.cpp with required dependencies

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/llama.cpp-model-manager.git
   cd llama.cpp-model-manager
   ```

2. Install dependencies:
   ```bash
   npm install
   ```

3. Configure the model manager:
   - Copy `models.example.json` to `models.json`
   - Update the model paths and settings in `models.json`
   - Set the preferred binary option in `.llama-web-config.json`

4. Start the development server:
   ```bash
   npm run dev
   ```

5. Access the web interface at `http://localhost:3000`

## Configuration

### Model Configuration (models.json)

The `models.json` file contains all model configurations. Each model entry includes:

- `model_path`: Path to the model file
- `name`: Human-readable model name
- `spec_type`: Speculative decoding type (`draft-simple`, `draft-mtp`, etc.)
- `gpu_layers`: Number of layers to offload to GPU
- `threads`: Number of CPU threads to use
- `cache_type_k/v`: KV cache type (e.g., `fp16`, `q4_0`)
- `ctx_size`: Context window size
- `batch_size`: Batch size for inference
- `ubatch_size`: Draft batch size
- `numa`: NUMA policy setting
- `flash_attn`: Flash attention setting

### Configuration Tips

- Use `draft-simple` for better acceptance rates with smaller models
- Set `gpu_layers` to `99` for maximum GPU acceleration
- Use `fp16` KV cache for optimal performance
- Adjust `ubatch_size` based on acceptance rate and speed requirements
- Enable `numa` with `isolate` policy for single-process latency

## Performance Analysis

The Llama.cpp Model Manager includes a powerful performance analysis system that automatically collects and analyzes model metrics. The system:

- Monitors generation speed (ms/token)
- Tracks draft acceptance rates (%)
- Calculates estimated speedup (x)
- Measures throughput (tokens/sec)

The analysis is based on empirical data collected from model instances and is used to provide recommendations for optimal configuration settings.

### Performance Report Summary

Based on recent performance analysis:

- **Top-performing model**: `qwen3.5-9B-glm5.1-distill-v1-q4-k-m`
  - Fastest generation: **561.7 ms/token** (2.2x faster than runner-up)
  - Best draft acceptance: **37.5%** (5.5% higher than runner-up)
  - Highest estimated speedup: **29.3x** (8.7x higher than runner-up)

- **Key findings**:
  - `draft-simple` configuration generally provides better speed/acceptance balance than `draft-mtp`
  - `fp16` KV cache typically offers optimal performance
  - Optimal `ubatch_size` varies by model and should be tested experimentally

## Usage

### Managing Models

1. Start the web interface
2. Add new models via the configuration file or web interface
3. Select a model to view its details
4. Start, stop, or configure the model
5. Monitor real-time performance metrics

### Analyzing Performance

1. Enable performance monitoring for a model
2. Collect metrics over time
3. Review the performance report
4. Implement recommended configuration changes
5. Re-test and validate improvements

### Optimizing Model Settings

1. Use the performance analysis to identify bottlenecks
2. Experiment with different configuration settings
3. Test changes on a subset of models
4. Apply successful configurations to all models
5. Document and share optimal settings

## Development

### Project Structure

```
llama.cpp-model-manager/
├── public/               # Static files
├── src/                  # Source code
│   ├── components/       # UI components
│   ├── pages/            # Web pages
│   ├── services/         # API and data services
│   └── utils/            # Utility functions
├── styles/               # CSS files
├── models.example.json   # Example model configuration
├── .llama-web-config.json # Web interface configuration
├── README.md             # This file
├── AGENTS.md             # Agent configuration
└── PERFORMANCE_REPORT.md # Performance analysis report
```

### Contributing

We welcome contributions! To contribute:

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to the branch
5. Open a pull request

Please ensure your code follows the project's style guidelines and includes appropriate tests.

### Testing

Run the test suite:
```bash
npm test
```

### Linting

The project uses ESLint for code quality. Run:
```bash
npm run lint
```

## Support

For help with the Llama.cpp Model Manager, please:
- Check the [documentation](#)
- Review the [performance analysis report](PERFORMANCE_REPORT.md)
- Consult the [model configuration examples](models.example.json)
- Open an issue on GitHub if you encounter problems

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.