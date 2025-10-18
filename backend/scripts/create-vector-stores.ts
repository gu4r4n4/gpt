#!/usr/bin/env tsx

import { Pool } from 'pg';
import OpenAI from 'openai';

// Validate required environment variables
const OPENAI_API_KEY = process.env.OPENAI_API_KEY;
const DATABASE_URL = process.env.DATABASE_URL;

if (!OPENAI_API_KEY) {
  console.error('Error: OPENAI_API_KEY environment variable is required');
  process.exit(1);
}

if (!DATABASE_URL) {
  console.error('Error: DATABASE_URL environment variable is required');
  process.exit(1);
}

// Initialize OpenAI client
const openai = new OpenAI({
  apiKey: OPENAI_API_KEY,
});

// Initialize database pool
const pool = new Pool({
  connectionString: DATABASE_URL,
});

// Main execution function
async function createVectorStores(): Promise<void> {
  let client;
  let hasErrors = false;

  try {
    client = await pool.connect();
    
    // Query for organizations missing vector stores
    const query = `
      SELECT o.id, o.name
      FROM public.organizations o
      LEFT JOIN public.org_vector_stores v ON v.org_id = o.id
      WHERE v.org_id IS NULL
      ORDER BY o.id
    `;
    
    const result = await client.query(query);
    const orgs = result.rows;
    
    if (orgs.length === 0) {
      console.log('No organizations need vector stores created.');
      return;
    }
    
    console.log(`Found ${orgs.length} organizations needing vector stores...`);
    
    // Process each organization
    for (const org of orgs) {
      try {
        console.log(`Creating vector store for org ${org.id} (${org.name})...`);
        
        // Create vector store via OpenAI API
        const vectorStore = await openai.beta.vectorStores.create({
          name: `org-${org.id}-offers`
        });
        
        // Insert into database
        const insertQuery = `
          INSERT INTO public.org_vector_stores (org_id, vector_store_id)
          VALUES ($1, $2)
        `;
        
        await client.query(insertQuery, [org.id, vectorStore.id]);
        
        console.log(`Created vector store for org ${org.id}: ${vectorStore.id}`);
        
      } catch (error) {
        console.error(`Failed to create vector store for org ${org.id}:`, error);
        hasErrors = true;
      }
    }
    
    if (hasErrors) {
      console.error('Some organizations failed to create vector stores.');
      process.exitCode = 1;
    } else {
      console.log('All vector stores created successfully.');
    }
    
  } catch (error) {
    console.error('Database connection or query error:', error);
    process.exitCode = 1;
  } finally {
    if (client) {
      client.release();
    }
    await pool.end();
  }
}

// Run the script
createVectorStores().catch((error) => {
  console.error('Unexpected error:', error);
  process.exitCode = 1;
});
