# SEM Crack Labeling

Local Label Studio project for SEM crack semantic and instance segmentation. Docker runs the full service on your machine, persists Label Studio state in `./label_studio_data`, and serves images only from local folders mounted into the container.

## Folder layout

```text
sem-crack-labeling/
  docker-compose.yml
  labeling_config.xml
  label_studio_data/
  data/
    images/
    sam_predictions/
  exports/
  scripts/
```

Place SEM images in `data/images`. Put optional SAM masks or preview images in `data/sam_predictions`. Nothing in this setup uploads data to a remote service.

## Startup

From this folder:

```bash
docker compose up -d
```

Open Label Studio at <http://localhost:8080>.

On first launch, create the local admin account in the browser. Then create a project and paste the contents of `labeling_config.xml` into **Settings -> Labeling Interface -> Code**.

## Import local images

Local file serving is enabled and restricted to the mounted `./data` folder. In Label Studio, create tasks whose image paths point to local files with this form:

```json
[
  {
    "data": {
      "image": "/data/local-files/?d=data/images/example.png",
      "sam_prediction": "/data/local-files/?d=data/sam_predictions/example_mask.png"
    }
  }
]
```

The `sam_prediction` field is optional. If you do not have SAM proposals, import tasks with only the `image` field.

## Shutdown

Stop the container while keeping all Label Studio data:

```bash
docker compose down
```

Restart later with:

```bash
docker compose up -d
```

## Backup

Back up Label Studio project state and annotations:

```bash
tar -czf exports/label_studio_data_backup_$(date +%Y%m%d_%H%M%S).tar.gz label_studio_data
```

Back up images separately if needed:

```bash
tar -czf exports/sem_labeling_inputs_$(date +%Y%m%d_%H%M%S).tar.gz data/images data/sam_predictions
```

## Export annotations

Use the Label Studio UI:

1. Open the project at <http://localhost:8080>.
2. Go to **Export**.
3. Choose the export format needed by the training pipeline, such as JSON, COCO, or brush mask exports.
4. Save exported files under `exports/`.

For segmentation training, verify the exported format preserves brush masks or polygon geometry for `crack`, `particle`, and `background/ignore`.

## Data policy

This project is local-only:

- Docker runs Label Studio on `localhost:8080`.
- Label Studio data persists in `./label_studio_data`.
- SEM images are read from `./data`.
- Exports are written to `./exports`.
- No cloud storage or external upload is configured.
