// Test script to verify automatic export functionality
const fs = require('fs');
const path = require('path');

// Simulate the exportMetrics function behavior
async function simulateExportMetrics(modelName, port) {
  console.log(`Simulating export for model: ${modelName} on port: ${port}`);
  
  // In a real scenario, this would fetch metrics from the model and export to CSV
  // For this test, we'll just create a sample CSV entry
  
  const csvDir = path.join(__dirname, 'metrics_exports');
  if (!fs.existsSync(csvDir)) {
    fs.mkdirSync(csvDir, { recursive: true });
  }
  
  const safeName = modelName.replace('/', '_').replace(' ', '_');
  const csvPath = path.join(csvDir, `${safeName}_metrics.csv`);
  
  // Sample data that would be exported
  const sampleRow = {
    timestamp: new Date().toISOString(),
    model: modelName,
    port: port,
    prompt_seconds_total: 10.5,
    prompt_tokens_total: 1000,
    prompt_eval_ms_per_token: 10.5,
    prompt_eval_rate_tokens_per_sec: 95.2,
    gen_seconds_total: 20.3,
    gen_tokens_total: 2000,
    gen_eval_ms_per_token: 10.15,
    gen_rate_tokens_per_sec: 98.0,
    spec_draft_tokens_total: 500,
    spec_accepted_tokens_total: 450,
    spec_drafts_total: 100,
    spec_acceptance_rate_pct: 90.0,
    spec_avg_draft_len: 5.0,
    spec_est_speedup: 1.9,
    threads: 8,
    threads_batch: 4,
    gpu_layers: 30,
    ctx_size: 4096,
    batch_size: 1,
    ubatch_size: 1,
    flash_attn: "on",
    numa: "distribute",
    numa_value: "distribute",
    parallel: "1",
    rope_scaling_type: "none",
    cache_type_k: "f16",
    cache_type_v: "f16",
    spec_type: "none",
    spec_draft_n_max: 1,
    spec_draft_n_min: 1,
    spec_draft_p_split: 0.5,
    spec_draft_p_min: 0.5,
    spec_ngram_size_n: 5,
    spec_ngram_size_m: 1,
    spec_ngram_min_hits: 1,
    spec_draft_type_k: "f16",
    spec_draft_type_v: "f16"
  };
  
  // Write/append to CSV
  const fileExists = fs.existsSync(csvPath);
  const fieldnames = Object.keys(sampleRow);
  
  const csvContent = fs.createWriteStream(csvPath, { flags: fileExists ? 'a' : 'w' });
  
  if (!fileExists) {
    // Write header
    csvContent.write(fieldnames.join(',') + '\n');
  }
  
  // Write row
  const rowValues = fieldnames.map(fieldName => {
    const value = sampleRow[fieldName];
    // Escape quotes and wrap in quotes if needed
    if (typeof value === 'string' && (value.includes(',') || value.includes('"') || value.includes('\n'))) {
      return `"${value.replace(/"/g, '""')}"`;
    }
    return value;
  });
  
  csvContent.write(rowValues.join(',') + '\n');
  csvContent.end();
  
  console.log(`Exported metrics to: ${csvPath}`);
  
  return {
    status: "exported",
    csv_path: csvPath,
    row: sampleRow
  };
}

// Test the automatic export functionality
async function testAutomaticExport() {
  console.log('Testing automatic export functionality...\n');
  
  // Simulate a running model
  const modelName = 'llama2-7b';
  const port = 8080;
  
  // Export metrics for the running model
  const result = await simulateExportMetrics(modelName, port);
  
  console.log('\nExport result:');
  console.log(JSON.stringify(result, null, 2));
  
  // Check if CSV file was created
  const csvDir = path.join(__dirname, 'metrics_exports');
  const safeName = modelName.replace('/', '_').replace(' ', '_');
  const csvPath = path.join(csvDir, `${safeName}_metrics.csv`);
  
  if (fs.existsSync(csvPath)) {
    console.log('\nCSV file contents:');
    const csvContent = fs.readFileSync(csvPath, 'utf8');
    console.log(csvContent);
  } else {
    console.log('\nCSV file was not created!');
  }
}

// Run the test
testAutomaticExport().catch(console.error);