Look at claim reviewer notebook and you might also search other notebooks for inspiration. We will now implement memory for claim reviewer agent.

# Storing thread ids
We have CosmosDB Serverless in our solution and I would like to use it to control processing of conversations. As conversation starts I would like you to create new record in new conversations collection (create collection if not exists) with id = thread id and processed = false. No more fields needed. 

# Batch processing
Write Python script that we will run as batch and do the following:
1. Find all records in CosmosDB collection that have processed set to false a process them one by one.
2. Lookup thread id in agent service and get messages from it and also timestamp when conversation happened.
3. Take all messages for thread and use AI Search to make it ready for vector search. Make sure all messages are stored as content in AI search and ID is thread ID and include timestamp from thread and timestamp of processing. See 2.document-vectorization.ipynb to see how we did something similar previously.
4. If conversation is successfully processed, mark it as processed = true on CosmosDB.

# Memory as tool
After this step enhance claim reviewer agent with new lookup tool that model can use to search historical conversations. For example when user reference something from the past (eg. I want to followup on our conversation about bikes, or when we was talking about crashed bike) agent would use the tool to retrieve that particular conversation from AI Search.

# Testing
Add new cell on end of claim_reviewer.ipynb to test memory retrieval. Use question that relates to our test conversation, eg. "When we talked about Honda Civic?".

#codebase 