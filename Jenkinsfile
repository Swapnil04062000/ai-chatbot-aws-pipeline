pipeline {
    agent any
    
    environment {
        // Your exact 12-digit AWS Account ID and region metrics
        AWS_ACCOUNT_ID = '344367179698' 
        AWS_REGION     = 'us-east-1'
        ECR_REPO_NAME  = 'ai-chatbot-repo'
        IMAGE_TAG      = "build-${BUILD_NUMBER}"
        ECR_REGISTRY   = "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
    }
    
    stages {
        stage('Source Checkout') {
            steps {
                echo 'Pulling code modifications from GitHub SCM...'
                checkout scm
            }
        }
        
        stage('Docker Image Build') {
            steps {
                echo 'Compiling project boundaries using multi-stage Docker & Astral uv...'
                script {
                    // Builds the image locally on your EC2 disk using the host's Docker engine
                    dockerImage = docker.build("${ECR_REGISTRY}/${ECR_REPO_NAME}:${IMAGE_TAG}")
                    sh "docker tag ${ECR_REGISTRY}/${ECR_REPO_NAME}:${IMAGE_TAG} ${ECR_REGISTRY}/${ECR_REPO_NAME}:latest"
                }
            }
        }
        
        stage('Artifact Registry Push') {
            steps {
                echo 'Logging into AWS ECR and pushing container images...'
                script {
                    // The Amazon ECR plugin manages authentication behind the scenes using your EC2 instance role
                    docker.withRegistry("https://${ECR_REGISTRY}") {
                        dockerImage.push()
                        dockerImage.push('latest')
                    }
                }
            }
        }
    }
    
    post {
        success {
            echo 'CI/CD Workflow pipeline executed successfully! Image pushed to AWS ECR.'
        }
        failure {
            echo 'Build pipeline failure detected. Inspect step tracing outputs.'
        }
    }
}
