import mongoose from 'mongoose';
import dotenv from 'dotenv';
dotenv.config();

const connectDB = async () => {
    try {
        const conn = await mongoose.connect(process.env.MONGO_URI || 'mongodb+srv://mismero:Neno1900@cluster0.lrd7lz9.mongodb.net/anivo_database?appName=Cluster0');
        console.log(`[Database] MongoDB Connected: ${conn.connection.host}`);
    } catch (error) {
        console.error(`[Database Error]: ${error.message}`);
        // Do not exit process, so the app can still try to scrape if DB is down
        // process.exit(1); 
    }
};

export default connectDB;
