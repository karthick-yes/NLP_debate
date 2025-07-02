# Use an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# --- NEW LINE ADDED HERE ---
# Install Playwright browser binaries and their system dependencies
RUN playwright install --with-deps chromium
# --- END NEW LINE ---

# Copy the rest of your application's code into the container
# This includes all your .py scripts and the run_pipeline.sh script
COPY . .

# Make the run_pipeline.sh script executable
RUN chmod +x run_pipeline.sh

# Set the entrypoint for the container. This command will be executed
# when the container starts. Any arguments passed to `docker run` will
# be appended to this command.
ENTRYPOINT ["./run_pipeline.sh"]

# Set a default command (useful for providing help text if no args are given)
CMD [""]