#!/usr/bin/env python3

import json
import subprocess
import sys
import click
from icecream import ic


def ask_ollama(question, json_content, model):
    """Ask the LLM to generate a jq command based on the question."""
    # Limit JSON content to first 50 lines
    json_content_limited = "\n".join(json_content.split("\n")[:50])
    if len(json_content.split("\n")) > 50:
        json_content_limited += "\n... (truncated for brevity)"
    
    # First, determine if it's an array or object
    try:
        json_data = json.loads(json_content)
        structure_type = "array" if isinstance(json_data, list) else "object"
    except:
        structure_type = "unknown"
    
    prompt = f"""
You are an expert in jq, the command-line JSON processor. Your task is to create a precise jq filter for the given question.

JSON content (first 50 lines):
{json_content_limited}

The JSON structure is: {structure_type}

Question: "{question}"

Respond ONLY with a valid jq filter that would answer this question.
No explanation, no backticks, no markdown formatting, just the raw filter.
The filter should be syntactically correct and directly usable in a jq command.

Important: 
- If the JSON is an array, remember to use '.[]' to iterate through array elements
- For counting unique values: unique | length
- For getting all unique values of a field in an array: '[.[].fieldName] | unique'
- For counting unique values in an array of objects: '[.[].fieldName] | unique | length'

Examples:
- To get all keys from a root object: keys
- To count array elements: length
- To get unique values of a field from array objects: '[.[].fieldName] | unique'
- To count unique values in array of objects: '[.[].fieldName] | unique | length'
- To access a specific field: .fieldName
- To access nested data: .parent.child
- To filter an array: .[] | select(.status == "active")
- To transform data: [.[] | {{name: .name, id: .id}}]
"""
    
    result = subprocess.run(
        ["ollama", "run", model, prompt],
        capture_output=True,
        text=True
    )
    
    # Clean up the response - remove backticks and other common formatting
    raw_result = result.stdout.strip()
    # Remove backticks, quotes, and other markdown formatting
    clean_result = raw_result.replace('`', '').replace('```', '').replace('jq', '').strip()
    
    return clean_result

def run_jq(jq_command, json_file):
    """Run jq command on the JSON file and return the result."""
    try:
        # Strip any quotes that might be in the command string
        cleaned_command = jq_command.strip('"\'')
        
        # Print the exact command for debugging
        cmd = ["jq", cleaned_command, json_file]
        click.echo(f"Executing: jq '{cleaned_command}' {json_file}")
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        
        if not result.stdout.strip():
            # If no output, try checking if the field exists
            click.echo("No output from jq. Checking JSON structure...")
            check_cmd = ["jq", "type", json_file]
            check_result = subprocess.run(check_cmd, capture_output=True, text=True)
            
            if "array" in check_result.stdout:
                # Try a simpler query on the first element to check field existence
                sample_cmd = ["jq", ".[0]", json_file]
                sample_result = subprocess.run(sample_cmd, capture_output=True, text=True)
                return f"No results found. Sample data from first element:\n{sample_result.stdout}"
            
            return "No results found. Check that the fields in your query exist in the JSON."
        
        return result.stdout
    except subprocess.CalledProcessError as e:
        return f"Error executing jq command: {e.stderr}"

def interactive_mode(file, model):
    """Run askjson in an interactive chat loop mode."""
    try:
        with open(file, 'r') as f:
            json_content = f.read()
            json.loads(json_content)
    except FileNotFoundError:
        click.echo(f"Error: File {file} not found", err=True)
        sys.exit(1)
    except json.JSONDecodeError:
        click.echo(f"Error: {file} is not a valid JSON file", err=True)
        sys.exit(1)
    
    click.echo(f"Interactive mode activated. Using model: {model}")
    click.echo(f"Analyzing JSON file: {file}")
    click.echo(f"Type 'exit' or 'quit' to end the session.")
    click.echo(f"Type 'jq' followed by a command to directly execute a jq query.")
    click.echo("-" * 50)
    
    while True:
        try:
            question = input("\nYour question (or jq command): ")
            
            if question.lower() in ['exit', 'quit']:
                click.echo("Exiting interactive mode.")
                break
                
            if question.lower().startswith('jq '):
                # Direct jq command mode
                jq_command = question[3:].strip()
                click.echo(f"Executing jq directly: {jq_command}")
                result = run_jq(jq_command, file)
                click.echo("\nResult:")
                click.echo(result)
                continue
                
            # Normal mode - ask LLM to generate jq command
            click.echo(f"Generating jq command for: {question}")
            jq_command = ask_ollama(question, json_content, model)
            click.echo(f"Generated jq command: {jq_command}")
            
            result = run_jq(jq_command, file)
            click.echo("\nResult:")
            click.echo(result)
            
            # Ask if user wants to modify the command
            modify = input("\nDo you want to modify the jq command? (y/n): ")
            if modify.lower() == 'y':
                new_command = input("Enter modified jq command: ")
                if new_command.strip():
                    click.echo(f"Using modified command: {new_command}")
                    new_result = run_jq(new_command, file)
                    click.echo("\nResult:")
                    click.echo(new_result)
        
        except KeyboardInterrupt:
            click.echo("\nExiting interactive mode.")
            break
        except Exception as e:
            click.echo(f"Error: {e}")

@click.command()
@click.option("--file", "-f", required=True, help="Path to the JSON file")
@click.option("--question", "-q", help="Question about the JSON data")
@click.option("--model", "-m", default="mistral:latest", help="Ollama model to use (default: mistral:latest)")
@click.option("--interactive", "-i", is_flag=True, help="Run in interactive mode")
def main(file, question, model, interactive):
    """Ask questions about JSON files using jq and Ollama."""
    
    if interactive:
        interactive_mode(file, model)
        return
    
    if not question:
        click.echo("Error: In non-interactive mode, you must provide a question", err=True)
        sys.exit(1)
    
    try:
        with open(file, 'r') as f:
            json_content = f.read()
            json.loads(json_content)
    except FileNotFoundError:
        click.echo(f"Error: File {file} not found", err=True)
        sys.exit(1)
    except json.JSONDecodeError:
        click.echo(f"Error: {file} is not a valid JSON file", err=True)
        sys.exit(1)
    
    # Get jq command from Ollama
    click.echo(f"Using model: {model}")
    click.echo(f"Generating jq command for question: {question}")
    jq_command = ask_ollama(question, json_content, model)
    click.echo(f"Generated jq command: {jq_command}")
    
    # Run jq command
    result = run_jq(jq_command, file)
    click.echo("\nResult:")
    click.echo(result)

if __name__ == "__main__":
    main()