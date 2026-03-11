#!/usr/bin/env bash
# A simple script to inspect the health status of a running container.
# Run this script with the container name as an argument, or without arguments
# to select from a list of running containers.


set -euo pipefail

inspect_health() {
  local container="$1"
  if [[ -z "$container" ]]; then
    echo "No container specified." >&2
    exit 1
  fi

  echo "Showing health logs for container: $container"
  docker inspect --format '{{json .State.Health}}' "$container" \
    | jq 'if . == null then
            {Error: "No healthcheck configured for container"}
          elif ((.Log // []) | length) == 0 then
            {Error: "No health logs present yet"}
          else
            .Log[]
            | {Start, End, ExitCode, Output: (.Output as $o | try ($o | fromjson) catch $o)}
          end'
}

# If container name was passed as argument, use it directly
if [[ $# -gt 0 ]]; then
  inspect_health "$1"
  exit 0
fi

# Otherwise, present a menu of running containers
containers=()
while IFS= read -r line; do
    containers+=("$line")
done < <(docker ps --format '{{.Names}}')

if [[ ${#containers[@]} -eq 0 ]]; then
  echo "No running containers found."
  exit 1
fi

echo "Select a container:"
select cname in "${containers[@]}"; do
  if [[ -n "$cname" ]]; then
    inspect_health "$cname"
    break
  else
    echo "Invalid selection. Try again."
  fi
done
