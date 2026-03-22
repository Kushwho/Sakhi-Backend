***

title: Python quickstart
excerpt: Submit batch jobs and stream predictions in real time using Hume's Python SDK.
---------------------------------------------------------------------------------------

This guide walks you through using Hume's Expression Measurement API with the
[Python SDK](https://github.com/humeai/hume-python-sdk). You will submit a batch job to analyze a media file and then
connect to the streaming API for real-time predictions.

## Setup

### Install the SDK

<Tabs>
  <Tab title="uv">
    <CodeBlock maxLines={0}>
      ```bash
      uv add hume
      ```
    </CodeBlock>
  </Tab>

  <Tab title="pip">
    <CodeBlock maxLines={0}>
      ```bash
      pip install hume
      ```
    </CodeBlock>
  </Tab>

  <Tab title="poetry">
    <CodeBlock maxLines={0}>
      ```bash
      poetry add hume
      ```
    </CodeBlock>
  </Tab>
</Tabs>

### Set your API key

Get your API key from the [Hume AI platform](https://platform.hume.ai/settings/keys) and set it as an environment
variable.

<CodeBlock maxLines={0}>
  ```bash
  export HUME_API_KEY=your_api_key_here
  ```
</CodeBlock>

### Create the client

<CodeBlock maxLines={0}>
  ```python
  import os
  from hume import HumeClient

  client = HumeClient(api_key=os.getenv("HUME_API_KEY"))
  ```
</CodeBlock>

## Batch API

The Batch API lets you submit files for processing and retrieve predictions when the job completes. This is best for
analyzing recordings, datasets, and other pre-recorded content.

### Submit a job

Start a job by specifying the models you want to run and the URLs of the files to process.

<CodeBlock maxLines={0}>
  ```python
  from hume.expression_measurement.batch.types import Models, Prosody

  job_id = client.expression_measurement.batch.start_inference_job(
      urls=["https://hume-tutorials.s3.amazonaws.com/faces.zip"],
      models=Models(
          prosody=Prosody(granularity="utterance"),
      ),
  )

  print(f"Job ID: {job_id}")
  ```
</CodeBlock>

<Callout intent="info">
  You can also upload local files instead of URLs. See the
  [API reference](/reference/expression-measurement-api/batch/start-inference-job-from-local-file) for the local
  file upload endpoint.
</Callout>

### Wait for the job to complete

Poll the job status until it reaches `COMPLETED` or `FAILED`.

<CodeBlock maxLines={0}>
  ```python
  import time

  while True:
      job_details = client.expression_measurement.batch.get_job_details(id=job_id)
      status = job_details.state.status

      if status == "COMPLETED":
          print("Job completed.")
          break
      elif status == "FAILED":
          print("Job failed.")
          break

      print(f"Status: {status}")
      time.sleep(3)
  ```
</CodeBlock>

<Callout intent="info">
  For production use, consider passing a `callback_url` when submitting the job. Hume will send a POST request to your
  URL when the job completes, eliminating the need to poll. The webhook payload includes the `job_id`, `status`, and
  `predictions`.
</Callout>

### Retrieve predictions

Once the job completes, retrieve and print the predictions.

<CodeBlock maxLines={0}>
  ```python
  predictions = client.expression_measurement.batch.get_job_predictions(id=job_id)

  for result in predictions:
      source = result.source
      print(f"\nSource: {source.url or source.filename}")

      for file_prediction in result.results.predictions:
          for group in file_prediction.models.prosody.grouped_predictions:
              for prediction in group.predictions:
                  print(f"\n  Text: {prediction.text}")
                  top_emotions = sorted(prediction.emotions, key=lambda e: e.score, reverse=True)[:3]
                  for emotion in top_emotions:
                      print(f"    {emotion.name}: {emotion.score:.3f}")
  ```
</CodeBlock>

<Callout intent="info">
  Predictions are also available as CSV files. Use the
  [Get job artifacts](/reference/expression-measurement-api/batch/get-job-artifacts) endpoint to download a zip
  archive containing one CSV per model.
</Callout>

## Streaming API

The Streaming API provides real-time predictions over a WebSocket connection. This is best for live audio, video, and
interactive applications.

### Connect and send data

Use the async client to open a streaming connection and send text for analysis.

<CodeBlock maxLines={0}>
  ```python
  import asyncio
  from hume import AsyncHumeClient
  from hume.expression_measurement.stream import Config, StreamLanguage

  async def stream_text():
      client = AsyncHumeClient(api_key=os.getenv("HUME_API_KEY"))

      async with client.expression_measurement.stream.connect() as socket:
          result = await socket.send_text(
              text="I am so excited to try this out!",
              config=Config(
                  language=StreamLanguage(granularity="sentence"),
              ),
          )

          language_predictions = result.language.predictions
          for prediction in language_predictions:
              print(f"Text: {prediction.text}")
              top_emotions = sorted(prediction.emotions, key=lambda e: e.score, reverse=True)[:3]
              for emotion in top_emotions:
                  print(f"  {emotion.name}: {emotion.score:.3f}")

  asyncio.run(stream_text())
  ```
</CodeBlock>

### Stream a file

You can also send audio or video files through the streaming connection.

<CodeBlock maxLines={0}>
  ```python
  import asyncio
  from hume import AsyncHumeClient
  from hume.expression_measurement.stream import Config

  async def stream_file():
      client = AsyncHumeClient(api_key=os.getenv("HUME_API_KEY"))

      async with client.expression_measurement.stream.connect() as socket:
          result = await socket.send_file(
              "sample.mp3",
              config=Config(prosody={}),
          )

          prosody_predictions = result.prosody.predictions
          for prediction in prosody_predictions:
              top_emotions = sorted(prediction.emotions, key=lambda e: e.score, reverse=True)[:3]
              for emotion in top_emotions:
                  print(f"  {emotion.name}: {emotion.score:.3f}")

  asyncio.run(stream_file())
  ```
</CodeBlock>

***
