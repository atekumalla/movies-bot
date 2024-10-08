from dotenv import load_dotenv
import chainlit as cl
import json
from movie_functions import get_now_playing_movies, get_showtimes, get_reviews, buy_ticket, confirm_ticket_purchase

load_dotenv()

# Note: If switching to LangSmith, uncomment the following, and replace @observe with @traceable
# from langsmith.wrappers import wrap_openai
# from langsmith import traceable
# client = wrap_openai(openai.AsyncClient())

from langfuse.decorators import observe
from langfuse.openai import AsyncOpenAI
 
client = AsyncOpenAI()

gen_kwargs = {
    "model": "gpt-4o",
    "temperature": 0.2,
    "max_tokens": 500
}

SYSTEM_PROMPT = """\
You are a helpful movie-related AI assistant. Your primary functions are:

1. Provide information about movie showtimes.
2. List movies currently playing at theaters near the user when given a zip code.
3. Assist with booking movie tickets based on user input.
4. Provide movie reviews for specific films upon request.

When asked about what movies are playing, just use the get_now_playing_movies() function.

When asked about showtimes or movies playing at a specific theater or location, always request the user's zip code if not provided. Use this information to give accurate, location-specific responses. Ensure that the 
location is a real location, if not ask the user to provide a zip code.

For ticket booking, gather necessary information such as:
- Movie title
- Preferred date and time
- Number of tickets
- Preferred theater (if applicable)

Once you have all required details, simulate the booking process and provide a confirmation summary.

When asked for a movie review, request the specific movie title if not provided. Offer a brief summary of critical reception, audience scores, and notable aspects of the film without spoiling key plot points.

Remember to be friendly, informative, and always prioritize the user's movie-going experience. If you're unsure about any specific showtime, availability, or review information, kindly inform the user that the information is subject to change and encourage them to double-check with official sources.

Here are some helpful functions you can use:
- get_now_playing_movies()
    This function lists all movies that are currently playing in theaters. 
- get_showtimes(title, location)
    This function provides showtimes for a specific movie in a given location where location is a zip code.
- get_reviews(movie_id)
    This function provides a brief summary of critical reception, audience scores, and notable aspects of the film without spoiling key plot points. Use the movie_id to get the reviews.
- confirm_ticket_purchase(theater, movie, showtime)
    This function is invoked when the user wants to purchase a ticket. It should ask the user for confirmation and then invoke the buy_ticket function if they confirm in the affirmative.
- buy_ticket(theater, movie, showtime)
    This function purchases the ticket booking process and provides a confirmation summary. Call this function only after the user confirms the ticket purchase with the confirm_ticket_purchase function.
    
If you need to make a function call, return only the following JSON format without any additional text.
{
    "function_name": "<function_name>",
    "arguments": "<arguments>"
}

For example:
{
    "function_name": "get_now_playing_movies",
    "arguments": ""
}

{
    "function_name": "get_showtimes",
    "arguments": {
        "title": "The Godfather",
        "location": "10001"
    }
}

When calling functions, ensure that the output contains only the JSON format, and no other extra strings.

"""

FETCH_REVIEW_PROMPT = """\
Based on the conversation, determine if the topic is about a specific movie. Determine if the user is asking a question that would be aided by knowing what critics are saying about the movie. Determine if the reviews for that movie have already been provided in the conversation. If so, do not fetch reviews.

Your only role is to evaluate the conversation, and decide whether to fetch reviews.

Output the current movie, id, a boolean to fetch reviews in JSON format, and your
rationale. Do not output as a code block.

{
    "movie": "title",
    "id": 123,
    "fetch_reviews": true
    "rationale": "reasoning"
}    
"""



@observe
@cl.on_chat_start
def on_chat_start():    
    message_history = [{"role": "system", "content": SYSTEM_PROMPT}]
    cl.user_session.set("message_history", message_history)

@observe
async def generate_response(client, message_history, gen_kwargs):
    response_message = cl.Message(content="")
    await response_message.send()

    stream = await client.chat.completions.create(messages=message_history, stream=True, **gen_kwargs)
    async for part in stream:
        if token := part.choices[0].delta.content or "":
            await response_message.stream_token(token)
    
    await response_message.update()

    return response_message

@cl.on_message
@observe
async def on_message(message: cl.Message):
    message_history = cl.user_session.get("message_history", [])
    message_history.append({"role": "user", "content": message.content})
    message_history = await movie_review_fetch_decider(message_history)
    response_message = await generate_response(client, message_history, gen_kwargs)

    # Check if the response is a JSON
    while True:
        try:
            function_call = json.loads(response_message.content)
            if isinstance(function_call, dict) and "function_name" in function_call and "arguments" in function_call:
                function_name = function_call["function_name"]
                arguments = function_call["arguments"]
                
                # Import the functions from movie_functions
                from movie_functions import get_now_playing_movies, get_showtimes, get_reviews, buy_ticket
                # Call the appropriate function based on the name
                if function_name == "get_now_playing_movies":
                    result = get_now_playing_movies()
                elif function_name == "get_showtimes":
                    title = arguments.get("title", "")
                    location = arguments.get("location", "")
                    result = get_showtimes(title, location)
                elif function_name == "get_reviews":
                    movie_id = arguments.get("movie_id", "")
                    result = get_reviews(movie_id)
                elif function_name == "confirm_ticket_purchase":
                    theater = arguments.get("theater", "")
                    movie = arguments.get("movie", "")
                    showtime = arguments.get("showtime", "")
                    result = confirm_ticket_purchase(theater, movie, showtime)
                elif function_name == "buy_ticket":
                    theater = arguments.get("theater", "")
                    movie = arguments.get("movie", "")
                    showtime = arguments.get("showtime", "")
                    result = buy_ticket(theater, movie, showtime)
                else:
                    result = f"Unknown function: {function_name}"
                # Add the result to the response_message

                # Append the function result to the message history
                message_history.append({"role": "system", "content": result})

                # Generate a new response based on the function result
                response_message = await generate_response(client, message_history, gen_kwargs)
                # Send the result as a new message
                # await cl.Message(content=response_message.content).send()
                message_history.append({"role": "assistant", "content": response_message.content})
                continue
            break  # Exit the loop if we successfully parsed the JSON
        except json.JSONDecodeError:
            # If it's not a JSON, generate a new response and continue the loop
            break

    cl.user_session.set("message_history", message_history)


async def movie_review_fetch_decider(message_history):
    # Check if the conversation is about a specific movie
    # Create a new message with FETCH_REVIEW_PROMPT
    fetch_review_message = [
        {"role": "system", "content": FETCH_REVIEW_PROMPT},
        {"role": "user", "content": json.dumps(message_history)}
    ]

    # Generate response for fetch review decision
    fetch_review_response = await generate_response(client, fetch_review_message, gen_kwargs)

    try:
        fetch_review_decision = json.loads(fetch_review_response.content)
        if isinstance(fetch_review_decision, dict) and fetch_review_decision.get("fetch_reviews", False):
            movie_id = fetch_review_decision.get("id")
            if movie_id:
                # Import get_reviews function
                from movie_functions import get_reviews
                
                # Call get_reviews function
                reviews = get_reviews(movie_id)
                
                # Append the reviews to message_history
                message_history.append({"role": "system", "content": reviews})
    except json.JSONDecodeError:
        # If the response is not a valid JSON, we'll skip the review fetching
        pass
    return message_history


if __name__ == "__main__":
    cl.main()
